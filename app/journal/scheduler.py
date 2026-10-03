"""Makes the daily and weekly reports when their period is over (spec C12), saves them and
sends them as a notice (and so to Telegram when that is on)."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from app.analytics.trades import TradeRecord
from app.journal.reports import Period, Report, ReportRepository, build_report, due_reports
from app.notify.events import EventKind, Notice

Log = Callable[[str, str], None]


def _quiet(level: str, message: str) -> None:
    return None


class ReportService:
    def __init__(
        self,
        repository: ReportRepository,
        trades: Callable[[], Sequence[TradeRecord]],
        *,
        account: Callable[[], str | None],
        offset: Callable[[], float],  # broker UTC offset in seconds
        currency: Callable[[], str] = lambda: "",
        notify: Callable[[Notice], object] | None = None,
        log: Log = _quiet,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository
        self._trades = trades
        self._account = account
        self._offset = offset
        self._currency = currency
        self._notify = notify
        self._log = log
        self._clock = clock

    def make(self, period: Period, *, send: bool = False) -> Report:
        account = self._account() or ""
        mine = [t for t in self._trades() if not account or t.account in ("", account)]
        report = build_report(
            period,
            account,
            mine,
            currency=self._currency(),
            **self._repository.inputs(period),
        )
        path = self._repository.save(report)
        where = f" ({path.name})" if path is not None else ""
        self._log("INFO", f"{period.label} report saved{where}")
        if send and self._notify is not None:
            self._notify(Notice(EventKind.DAILY_REPORT, report.text, f"report:{report.id}"))
        return report

    def run_due(self) -> list[Report]:
        """Called every few minutes: the reports whose period ended and that are missing."""
        account = self._account()
        if not account:
            return []
        try:
            periods = due_reports(
                self._clock(),
                self._offset(),
                lambda kind, first: self._repository.done(account, kind, first),
            )
            return [self.make(period, send=True) for period in periods]
        except Exception as error:
            self._log("ERROR", f"The report could not be made: {type(error).__name__}: {error}")
            return []
