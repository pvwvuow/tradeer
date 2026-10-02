"""The MT5 helper process (ADR 46): the only place in the app that imports MetaTrader5.

The `MetaTrader5` package keeps Python's global interpreter lock while it waits for the
terminal. In the app process one slow call (a first history download took 15 s on a real PC)
froze every thread: the window, the analysis and even the watchdog. So the package runs in a
small helper process instead. The gateway thread sends each call through a pipe and waits
without holding the lock, and a call that hangs is ended by restarting the helper; the
connection monitor then logs in again.

Results come back as plain values. Named records (AccountInfo, SymbolInfo, Tick, TradeDeal and
so on) are rebuilt as named tuples with the same fields, so they read exactly like the
package's own; numpy rate arrays travel unchanged.
"""

from __future__ import annotations

import contextlib
import importlib
import multiprocessing
import os
import threading
import time
from collections import namedtuple
from collections.abc import Callable
from functools import lru_cache, partial
from multiprocessing.process import BaseProcess
from typing import Any, NamedTuple

from app.mt5.errors import MT5Error, MT5Timeout, MT5Unavailable

CALL_TIMEOUT_SECONDS = 120.0
START_TIMEOUT_SECONDS = 60.0
STOP_TIMEOUT_SECONDS = 5.0
WAIT_SLICE_SECONDS = 1.0
PROCESS_NAME = "mt5-helper"

RESTART_FIX = (
    "The app restarts its MT5 helper by itself and logs in again. If this keeps happening, "
    "restart MT5 and the app."
)
INSTALL_FIX = (
    "Reinstall the app. The build must contain the MetaTrader5 package (run --self-check)."
)

Reply = tuple[str, Any]


class _Record(NamedTuple):
    """A named result on its way through the pipe."""

    kind: str
    fields: tuple[str, ...]
    values: tuple[Any, ...]
    nested: bool


# One shared tuple per record shape, so a pickled list of records stores the field names once.
_SHAPES: dict[tuple[str, ...], tuple[str, ...]] = {}


def encode(value: Any) -> Any:
    """Helper side: turn package results into values the app can unpickle without the package."""
    if value is None or isinstance(value, bool | int | float | str | bytes):
        return value
    as_dict = getattr(value, "_asdict", None)
    if callable(as_dict):
        mapping = as_dict()
        fields = tuple(mapping)
        fields = _SHAPES.setdefault(fields, fields)
        values = tuple(map(encode, mapping.values()))
        nested = any(isinstance(item, _Record | tuple | list | dict) for item in values)
        return _Record(type(value).__name__, fields, values, nested)
    if isinstance(value, tuple):
        return tuple(map(encode, value))
    if isinstance(value, list):
        return [encode(item) for item in value]
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    return value  # numpy arrays and numpy scalars pickle as they are


_make_type: Callable[..., Any] = namedtuple


@lru_cache(maxsize=128)
def _record_type(kind: str, fields: tuple[str, ...]) -> Any:
    name = kind if kind.isidentifier() and not kind.startswith("_") else "Record"
    return _make_type(name, fields, rename=True)


def decode(value: Any) -> Any:
    """App side: rebuild named records as named tuples with the package's field names."""
    if isinstance(value, _Record):
        values = tuple(map(decode, value.values)) if value.nested else value.values
        return _record_type(value.kind, value.fields)._make(values)
    if isinstance(value, tuple):
        return tuple(map(decode, value))
    if isinstance(value, list):
        return [decode(item) for item in value]
    if isinstance(value, dict):
        return {key: decode(item) for key, item in value.items()}
    return value


# Helper process ---------------------------------------------------------------------------
def load_package(loader: str | None) -> Any:
    """The real package, or a stand-in named `module` or `module:factory` (tests only)."""
    if loader is None:
        import MetaTrader5

        return MetaTrader5
    module_name, _, factory = loader.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, factory)() if factory else module


def _version(package: Any) -> str:
    return str(getattr(package, "__version__", "unknown"))


def _reply(package: Any, message: tuple[str, str, tuple[Any, ...], dict[str, Any]]) -> Reply:
    kind, name, args, kwargs = message
    if kind == "ping":
        return "ok", (os.getpid(), _version(package))
    function = getattr(package, name, None)
    if not callable(function):
        return "missing", f"MetaTrader5 has no function {name!r}"
    try:
        return "ok", encode(function(*args, **kwargs))
    except Exception as error:
        return "error", f"{type(error).__name__}: {error}"


def helper_main(conn: Any, loader: str | None = None) -> None:
    """Entry point of the helper: import the package, then answer calls until the app stops."""
    try:
        package = load_package(loader)
    except BaseException as error:  # a broken install can raise anything, even SystemExit
        with contextlib.suppress(Exception):
            conn.send(("failed", f"{type(error).__name__}: {error}"))
        return
    conn.send(("ready", _version(package)))
    while True:
        try:
            message = conn.recv()
        except (EOFError, OSError):
            return  # the app closed the pipe or ended
        if message is None:
            return
        reply = _reply(package, message)
        try:
            conn.send(reply)
        except (EOFError, OSError):
            return
        except Exception as error:  # the result could not be pickled
            conn.send(("error", f"the result could not be sent: {type(error).__name__}: {error}"))


# App side ---------------------------------------------------------------------------------
class MT5Process:
    """Implements `MT5Api` by forwarding every call to the helper process.

    `mt5.account_info()` becomes one round trip through the pipe. While it waits, the calling
    thread holds no lock and calls `on_wait` about once a second (the gateway beats the
    watchdog there). A call that takes longer than `call_timeout` ends the helper; the next
    call starts a new one.
    """

    def __init__(
        self,
        loader: str | None = None,
        *,
        call_timeout: float = CALL_TIMEOUT_SECONDS,
        start_timeout: float = START_TIMEOUT_SECONDS,
        wait_slice: float = WAIT_SLICE_SECONDS,
        on_wait: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._loader = loader
        self._call_timeout = call_timeout
        self._start_timeout = start_timeout
        self._wait_slice = wait_slice
        self.on_wait = on_wait
        self._clock = clock
        self._lock = threading.RLock()
        self._process: BaseProcess | None = None
        self._conn: Any = None
        self._version = ""
        self.starts = 0

    @property
    def pid(self) -> int | None:
        process = self._process
        return process.pid if process is not None else None

    @property
    def running(self) -> bool:
        process = self._process
        return process is not None and process.is_alive()

    def start(self) -> str:
        """Start the helper (if needed) and wait until it imported the package.

        Returns the package version. Raises `MT5Unavailable` when the helper cannot start or
        the package cannot be imported.
        """
        with self._lock:
            if self._process is not None:
                return self._version
            context = multiprocessing.get_context("spawn")
            parent, child = context.Pipe()
            process = context.Process(
                target=helper_main,
                args=(child, self._loader),
                name=PROCESS_NAME,
                daemon=True,
            )
            try:
                process.start()
            except Exception as error:
                parent.close()
                child.close()
                raise MT5Unavailable(
                    "The MT5 helper process could not start",
                    INSTALL_FIX,
                    detail=f"{type(error).__name__}: {error}",
                ) from error
            child.close()  # the helper holds its own copy; closing ours lets us see it end
            self._process, self._conn = process, parent
            try:
                status, payload = self._receive("start", self._start_timeout)
            except MT5Error as error:
                raise MT5Unavailable(
                    "The MT5 helper process did not start",
                    INSTALL_FIX,
                    detail=error.title,
                ) from error
            if status != "ready":
                self._end()
                raise MT5Unavailable(
                    "The MetaTrader5 package could not be loaded",
                    INSTALL_FIX,
                    detail=str(payload),
                )
            self._version = str(payload)
            self.starts += 1
            return self._version

    def ping(self) -> tuple[int, str]:
        """The helper's process id and the package version (used by `--self-check`)."""
        pid, version = self._ask("ping", "ping", (), {})
        return int(pid), str(version)

    def invoke(self, function: str, /, *args: Any, **kwargs: Any) -> Any:
        """Call one package function in the helper: `invoke("symbol_info", "EURUSD")`."""
        return self._ask("call", function, args, kwargs)

    def close(self) -> None:
        """Stop the helper. The gateway calls this when it stops."""
        with self._lock:
            conn, process = self._conn, self._process
            self._conn = self._process = None
        if conn is None or process is None:
            return
        with contextlib.suppress(Exception):
            conn.send(None)
        process.join(STOP_TIMEOUT_SECONDS)
        if process.is_alive():
            process.kill()
            process.join(STOP_TIMEOUT_SECONDS)
        with contextlib.suppress(Exception):
            conn.close()

    def __getattr__(self, name: str) -> Callable[..., Any]:
        # Only called for names this class does not define: the package's functions.
        if name.startswith("_"):
            raise AttributeError(name)
        return partial(self.invoke, name)

    # Internals ----------------------------------------------------------------------------
    def _ask(self, kind: str, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        with self._lock:
            if self._process is None:
                self.start()
            try:
                self._conn.send((kind, name, args, kwargs))
            except (EOFError, OSError) as error:
                self._end()
                raise MT5Error("The MT5 helper process stopped", RESTART_FIX) from error
            status, payload = self._receive(name, self._call_timeout)
        if status == "ok":
            return decode(payload)
        if status == "missing":
            raise AttributeError(payload)
        raise MT5Error(f"MT5 {name} failed: {payload}", RESTART_FIX)

    def _receive(self, name: str, timeout: float) -> Reply:
        conn, process = self._conn, self._process
        deadline = self._clock() + timeout
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                self._end()
                raise MT5Timeout(
                    f"MT5 did not answer within {timeout:g} s ({name}); the helper was restarted",
                    RESTART_FIX,
                )
            try:
                ready = bool(conn.poll(min(self._wait_slice, remaining)))
            except (EOFError, OSError):
                ready = True  # the pipe broke: recv() below reports it
            if ready:
                try:
                    reply: Reply = conn.recv()
                except (EOFError, OSError) as error:
                    code = self._end()
                    raise MT5Error(
                        f"The MT5 helper process stopped (exit code {code})",
                        RESTART_FIX,
                    ) from error
                return reply
            if process is not None and not process.is_alive():
                code = self._end()
                raise MT5Error(f"The MT5 helper process stopped (exit code {code})", RESTART_FIX)
            if self.on_wait is not None:
                with contextlib.suppress(Exception):
                    self.on_wait()

    def _end(self) -> int | None:
        """Kill the helper after a hang or a crash; the next call starts a new one."""
        conn, process = self._conn, self._process
        self._conn = self._process = None
        code: int | None = None
        if process is not None:
            if process.is_alive():
                process.kill()
            process.join(STOP_TIMEOUT_SECONDS)
            code = process.exitcode
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()
        return code
