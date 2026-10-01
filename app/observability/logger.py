"""loguru wiring (spec E3): one non-blocking file handler, the in-app tail, optional console.

Use `get_logger(LogCategory.X)` in every module. Pass dynamic text as an argument, never as
part of the format string: `log.info("Order sent: {}", text)`. Extra fields go through
`log.bind(symbol="EURUSD")` or keyword arguments.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, cast

from loguru import logger as _loguru

from app.observability.buffer import RecentLogBuffer
from app.observability.categories import DEFAULT_CATEGORY, LogCategory
from app.observability.files import LogFileWriter
from app.observability.levels import LevelRegistry
from app.observability.masking import SecretMasker
from app.observability.records import (
    CATEGORY_KEY,
    DROP_KEY,
    ENTRY_KEY,
    JSON_KEY,
    LINE_KEY,
    RecordPatcher,
)

if TYPE_CHECKING:
    from loguru import Logger, Message, Record

EntrySink = Callable[[Mapping[str, Any]], None]
EntryFilter = Callable[[int, str], bool]


def get_logger(category: LogCategory) -> Logger:
    """A logger whose records go to `category`. Safe to create at import time."""
    return _loguru.bind(category=category.value)


def audit(
    action: str,
    *,
    before: object = None,
    after: object = None,
    source: str = "user",
) -> None:
    """Audit log (spec E3): every user action and setting change, before and after."""
    log = _loguru.bind(
        category=LogCategory.AUDIT.value,
        action=action,
        source=source,
        before=before,
        after=after,
    )
    log.opt(depth=1).info("Audit: {}", action)


def log_startup(details: Mapping[str, object]) -> None:
    """Startup log (spec E3): versions, profile and settings. Never pass secrets here."""
    get_logger(LogCategory.APP).bind(**dict(details)).opt(depth=1).info("Application started")


def _line_format(record: Record) -> str:
    # A callable format: loguru then never appends the raw `{exception}` to the output.
    return "{extra[" + LINE_KEY + "]}\n"


def _accepted(record: Record) -> bool:
    return DROP_KEY not in record["extra"]


class _FileSink:
    def __init__(self, writer: LogFileWriter) -> None:
        self._writer = writer

    def __call__(self, message: Message) -> None:
        extra = message.record["extra"]
        self._writer.write(str(extra[CATEGORY_KEY]), str(extra[JSON_KEY]), str(extra[LINE_KEY]))


class _BufferSink:
    def __init__(self, buffer: RecentLogBuffer) -> None:
        self._buffer = buffer

    def __call__(self, message: Message) -> None:
        self._buffer.append(message.record["extra"][ENTRY_KEY])


class _EntrySink:
    def __init__(self, sink: EntrySink) -> None:
        self._sink = sink

    def __call__(self, message: Message) -> None:
        self._sink(message.record["extra"][ENTRY_KEY])


class LogPipeline:
    """Owns the loguru handlers for one run. `start()` replaces every existing handler."""

    def __init__(
        self,
        writer: LogFileWriter,
        buffer: RecentLogBuffer,
        registry: LevelRegistry,
        masker: SecretMasker,
        *,
        enqueue: bool = True,
        console: bool = False,
    ) -> None:
        self.writer = writer
        self.buffer = buffer
        self.registry = registry
        self.masker = masker
        self._enqueue = enqueue
        self._console = console
        self._handler_ids: list[int] = []

    def start(self) -> None:
        patcher = RecordPatcher(self.registry, self.masker)

        def patch(record: Record) -> None:
            patcher(cast(dict[str, Any], record))

        _loguru.remove()
        _loguru.configure(patcher=patch, extra={CATEGORY_KEY: DEFAULT_CATEGORY.value})
        # One file handler for every category keeps one writer thread instead of seventeen.
        file_handler = _loguru.add(
            _FileSink(self.writer),
            level=0,
            format=_line_format,
            filter=_accepted,
            enqueue=self._enqueue,
            backtrace=False,
            diagnose=False,
            catch=True,
        )
        # The tail for the Logs page and crash reports is in memory and synchronous, so a
        # crash report always holds the lines logged just before the crash.
        buffer_handler = _loguru.add(
            _BufferSink(self.buffer),
            level=0,
            format=_line_format,
            filter=_accepted,
            enqueue=False,
            backtrace=False,
            diagnose=False,
            catch=True,
        )
        self._handler_ids = [file_handler, buffer_handler]
        if self._console and sys.stderr is not None:
            console_handler = _loguru.add(
                sys.stderr,
                level=0,
                format=_line_format,
                filter=_accepted,
                colorize=False,
                backtrace=False,
                diagnose=False,
                catch=True,
            )
            self._handler_ids.append(console_handler)

    def add_entry_sink(self, sink: EntrySink, accept: EntryFilter) -> int:
        """Send masked entries that pass `accept(level_no, category)` to `sink`.

        The sink runs in its own worker thread (when the pipeline enqueues), so a slow sink
        such as the database never blocks the thread that logs.
        """

        def keep(record: Record) -> bool:
            if not _accepted(record):
                return False
            return accept(int(record["level"].no), str(record["extra"][CATEGORY_KEY]))

        handler = _loguru.add(
            _EntrySink(sink),
            level=0,
            format=_line_format,
            filter=keep,
            enqueue=self._enqueue,
            backtrace=False,
            diagnose=False,
            catch=True,
        )
        self._handler_ids.append(handler)
        return handler

    def remove_sink(self, handler_id: int) -> None:
        with contextlib.suppress(ValueError):
            _loguru.remove(handler_id)
        if handler_id in self._handler_ids:
            self._handler_ids.remove(handler_id)

    def flush(self) -> None:
        """Wait until every queued record has been written."""
        _loguru.complete()

    def stop(self) -> None:
        for handler_id in self._handler_ids:
            with contextlib.suppress(ValueError):
                _loguru.remove(handler_id)
        self._handler_ids = []
        self.writer.close()
