"""The MT5 gateway thread (spec D3.1): every MT5 call of the app goes through it.

The `MetaTrader5` package is not thread-safe, so one dedicated thread owns every call. Other
threads submit work through a queue and get a `Future` back; every request has a timeout and
is reported to `on_request` (the app logs it to the `mt5` category). The package itself runs
in the MT5 helper process (`terminal_process.py`, ADR 46), so a slow call never blocks the
app's other threads.
"""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any, TypeVar, cast

from app.mt5.api import MT5Api
from app.mt5.errors import MT5Error, MT5Timeout, MT5Unavailable
from app.mt5.terminal_process import MT5Process
from app.observability.masking import MASKER

T = TypeVar("T")

DEFAULT_TIMEOUT_SECONDS = 30.0
SLOW_REQUEST_MS = 1000.0
THREAD_NAME = "mt5-gateway"


SKIPPED_ERRORS = ("cancelled before it started", "expired in the queue")


def load_mt5() -> MT5Api:
    """Start the MT5 helper process with the real package. Called in the gateway thread."""
    process = MT5Process()
    process.start()
    return cast(MT5Api, process)


@dataclass(frozen=True)
class RequestRecord:
    name: str
    duration_ms: float
    ok: bool
    error: str | None
    arguments: Mapping[str, Any]
    queued_ms: float

    @property
    def skipped(self) -> bool:
        """The request never ran: its caller gave up, or it waited too long behind others."""
        return self.error in SKIPPED_ERRORS


@dataclass(frozen=True)
class ActiveRequest:
    name: str
    seconds: float


@dataclass
class _Request:
    name: str
    work: Callable[[MT5Api], Any]
    future: Future[Any]
    deadline: float
    queued_at: float
    arguments: Mapping[str, Any]


_STOP = object()


class MT5Gateway:
    def __init__(
        self,
        api_factory: Callable[[], MT5Api] = load_mt5,
        *,
        default_timeout: float = DEFAULT_TIMEOUT_SECONDS,
        on_request: Callable[[RequestRecord], None] | None = None,
        heartbeat: Callable[[], None] | None = None,
        idle_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api_factory = api_factory
        self._default_timeout = default_timeout
        self._on_request = on_request
        self._heartbeat = heartbeat
        self._idle_seconds = idle_seconds
        self._clock = clock
        self._queue: queue.Queue[_Request | object] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._load_error: MT5Error | None = None
        self._running = False
        self._active: tuple[str, float] | None = None

    @property
    def running(self) -> bool:
        return self._running

    def busy(self) -> ActiveRequest | None:
        """The request that runs right now and for how long, or None when the gateway is idle."""
        active = self._active
        if active is None:
            return None
        return ActiveRequest(active[0], self._clock() - active[1])

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._running = True
            self._thread = threading.Thread(target=self._run, name=THREAD_NAME, daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Shut MT5 down in the gateway thread and wait for the thread to end."""
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            self._queue.put(_STOP)
        thread.join(timeout)
        with self._lock:
            self._thread = None
            self._running = False

    def submit(
        self,
        name: str,
        work: Callable[[MT5Api], T],
        *,
        timeout: float | None = None,
        arguments: Mapping[str, Any] | None = None,
    ) -> Future[T]:
        future: Future[T] = Future()
        if not self._running:
            future.set_exception(MT5Error("The MT5 gateway is not running", "Restart the app."))
            return future
        now = self._clock()
        limit = self._default_timeout if timeout is None else timeout
        request = _Request(name, work, cast(Future[Any], future), now + limit, now, arguments or {})
        self._queue.put(request)
        return future

    def run(
        self,
        name: str,
        work: Callable[[MT5Api], T],
        *,
        timeout: float | None = None,
        arguments: Mapping[str, Any] | None = None,
    ) -> T:
        """Submit `work` and wait for its result. Raises `MT5Timeout` after `timeout`."""
        limit = self._default_timeout if timeout is None else timeout
        future = self.submit(name, work, timeout=limit, arguments=arguments)
        try:
            return future.result(timeout=limit)
        except FutureTimeout:
            future.cancel()
            raise MT5Timeout(
                f"MT5 did not answer within {limit:g} s ({name})",
                "MT5 may be frozen or still starting. Wait, then press Re-check.",
            ) from None

    def call(self, method: str, *args: Any, timeout: float | None = None, **kwargs: Any) -> Any:
        """Call one package function by name, for example `call("symbol_info", "EURUSD")`."""

        def work(mt5: MT5Api) -> Any:
            return getattr(mt5, method)(*args, **kwargs)

        shown = {"args": list(args), **kwargs} if args else dict(kwargs)
        return self.run(method, work, timeout=timeout, arguments=shown)

    def _run(self) -> None:
        api = self._load()
        while True:
            try:
                item = self._queue.get(timeout=self._idle_seconds)
            except queue.Empty:
                self._beat()
                continue
            if item is _STOP:
                break
            self._execute(api, cast(_Request, item))
            self._beat()
        if api is not None:
            with contextlib.suppress(Exception):
                api.shutdown()
            helper = cast(object, api)
            if isinstance(helper, MT5Process):
                helper.close()
        self._running = False
        self._fail_pending()

    def _load(self) -> MT5Api | None:
        try:
            api = self._api_factory()
        except Exception as error:
            self._load_error = MT5Unavailable(
                "The MetaTrader5 package could not be loaded",
                "Reinstall the app. The build must contain the MetaTrader5 package (run "
                "--self-check).",
                detail=f"{type(error).__name__}: {error}",
            )
            return None
        helper = cast(object, api)
        if isinstance(helper, MT5Process):
            helper.on_wait = self._beat  # beat while a slow call runs in the helper
        return api

    def _execute(self, api: MT5Api | None, request: _Request) -> None:
        started = self._clock()
        queued_ms = (started - request.queued_at) * 1000.0
        if not request.future.set_running_or_notify_cancel():
            self._report(request, 0.0, False, "cancelled before it started", queued_ms)
            return
        if started > request.deadline:
            error = MT5Timeout("The request waited too long in the queue", "Press Re-check.")
            request.future.set_exception(error)
            self._report(request, 0.0, False, "expired in the queue", queued_ms)
            return
        if api is None:
            request.future.set_exception(self._load_error or MT5Unavailable("No MT5", ""))
            self._report(request, 0.0, False, "MetaTrader5 package not loaded", queued_ms)
            return
        self._active = (request.name, started)
        try:
            result = request.work(api)
        except BaseException as error:
            self._active = None
            request.future.set_exception(error)
            duration = (self._clock() - started) * 1000.0
            self._report(request, duration, False, f"{type(error).__name__}: {error}", queued_ms)
            return
        self._active = None
        request.future.set_result(result)
        self._report(request, (self._clock() - started) * 1000.0, True, None, queued_ms)

    def _report(
        self,
        request: _Request,
        duration_ms: float,
        ok: bool,
        error: str | None,
        queued_ms: float,
    ) -> None:
        if self._on_request is None:
            return
        arguments = MASKER.mask_value(dict(request.arguments))
        record = RequestRecord(request.name, duration_ms, ok, error, arguments, queued_ms)
        with contextlib.suppress(Exception):
            self._on_request(record)

    def _beat(self) -> None:
        if self._heartbeat is not None:
            with contextlib.suppress(Exception):
                self._heartbeat()

    def _fail_pending(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            if isinstance(item, _Request) and item.future.set_running_or_notify_cancel():
                item.future.set_exception(MT5Error("The MT5 gateway stopped", "Restart the app."))
