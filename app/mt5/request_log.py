"""Log every MT5 request and connection event to the `mt5` category (spec E3, I4)."""

from __future__ import annotations

from app.mt5.gateway import SLOW_REQUEST_MS, RequestRecord
from app.observability.categories import LogCategory
from app.observability.logger import get_logger

_log = get_logger(LogCategory.MT5)


def log_request(record: RequestRecord) -> None:
    log = _log.bind(
        request=record.name,
        duration_ms=round(record.duration_ms, 1),
        queued_ms=round(record.queued_ms, 1),
        arguments=dict(record.arguments),
    )
    if record.skipped:
        # Not an MT5 failure: another request held the gateway (ADR 46).
        log.debug("MT5 {} skipped: {}", record.name, record.error)
    elif not record.ok:
        log.warning(
            "MT5 {} failed after {:.0f} ms: {}",
            record.name,
            record.duration_ms,
            record.error,
        )
    elif record.duration_ms >= SLOW_REQUEST_MS:
        log.warning("MT5 {} was slow: {:.0f} ms", record.name, record.duration_ms)
    else:
        log.debug("MT5 {} took {:.0f} ms", record.name, record.duration_ms)


def log_event(level: str, message: str) -> None:
    _log.opt(depth=1).log(level, "{}", message)
