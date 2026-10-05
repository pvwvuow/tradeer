"""Fixes from the PC log of 5 October 2026 (0.22.1): the first minutes after the Sunday
open, a short MT5 reconnect, the weekly report's health list and an AI endpoint behind
Cloudflare."""

import sqlite3
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from typing import Any, cast

from app.analysis.sessions import market_open, week_opened_at
from app.analytics.llm_client import LlmSettings, ask, http_error_text
from app.engine.health_monitor import HealthMonitor
from app.journal.reports import ReportRepository, week_period
from app.mt5.connection import ConnectionState, ConnectionStatus
from app.notify.center import NotificationCenter
from app.notify.settings import NotificationSettings
from app.notify.watcher import NoticeWatcher
from app.observability.health import HealthInputs, HealthStatus, quotes_check
from app.storage.repositories import Store
from app.storage.signal_store import iso_time


def utc(*parts: int) -> float:
    return datetime(*parts, tzinfo=UTC).timestamp()


SUNDAY_OPEN = utc(2026, 10, 4, 21, 0)  # 17:00 in New York (summer time)
FRIDAY_TICK = utc(2026, 10, 2, 20, 54)


def test_the_fx_week_opens_on_sunday_at_17_new_york_time() -> None:
    assert week_opened_at(utc(2026, 10, 4, 21, 0, 21)) == SUNDAY_OPEN
    assert week_opened_at(utc(2026, 10, 7, 12, 0)) == SUNDAY_OPEN  # Wednesday
    assert week_opened_at(utc(2026, 10, 3, 12, 0)) is None  # Saturday: closed
    assert week_opened_at(utc(2026, 11, 2, 10, 0)) == utc(2026, 11, 1, 22, 0)  # winter time
    assert market_open(SUNDAY_OPEN) and not market_open(SUNDAY_OPEN - 1)


def at_the_open(minutes: float, newest: float) -> HealthInputs:
    return HealthInputs(
        now=SUNDAY_OPEN + minutes * 60,
        mt5_state="connected",
        quote_times=(newest, newest - 30, newest - 60),
        market_opened_at=SUNDAY_OPEN,
    )


def test_friday_prices_are_not_stale_in_the_first_minutes_of_the_week() -> None:
    # The log: 21 s after the open the newest price was Friday's, "173,123 s old: critical".
    first = quotes_check(at_the_open(0.35, FRIDAY_TICK))
    assert first.status is HealthStatus.OK
    assert "since the market opened" in first.text
    assert quotes_check(at_the_open(16, FRIDAY_TICK)).status is HealthStatus.WARNING
    assert quotes_check(at_the_open(26, FRIDAY_TICK)).status is HealthStatus.CRITICAL
    fresh = quotes_check(at_the_open(7, SUNDAY_OPEN + 7 * 60 - 12))
    assert fresh.status is HealthStatus.OK
    assert fresh.text.startswith("The newest price is 12 s old")
    stopped = quotes_check(at_the_open(180, SUNDAY_OPEN + 3600))
    assert stopped.status is HealthStatus.CRITICAL  # prices of this week that stopped


def test_the_monitor_adds_the_week_open() -> None:
    now = SUNDAY_OPEN + 21
    inputs = HealthInputs(now=now, mt5_state="connected", quote_times=(FRIDAY_TICK,))
    monitor = HealthMonitor(lambda: inputs, clock=lambda: now)
    quotes = next(check for check in monitor.run_once().checks if check.name == "quotes_fresh")
    assert quotes.status is HealthStatus.OK
    logged: list[str] = []
    early = HealthMonitor(
        lambda: HealthInputs(now=1000.0),
        clock=lambda: 1000.0,
        log=lambda level, message: logged.append(message),
    )
    early.run_once()
    assert not [text for text in logged if "inputs incomplete" in text]  # 1970 is fine too


class Timers:
    def __init__(self) -> None:
        self.waiting: list[tuple[float, Callable[[], None]]] = []

    def __call__(self, delay: float, action: Callable[[], None]) -> object:
        self.waiting.append((delay, action))
        return None

    def fire(self) -> None:
        found, self.waiting = self.waiting, []
        for _delay, action in found:
            action()


def watcher() -> tuple[NoticeWatcher, list[str], Timers]:
    sent: list[str] = []
    center = NotificationCenter(
        lambda: NotificationSettings(),
        toast=lambda notice: sent.append(notice.text),
        telegram=lambda notice: None,
        local=lambda: datetime(2026, 10, 5, 0, 36),
    )
    timers = Timers()
    return NoticeWatcher(center, schedule=timers), sent, timers


UP = ConnectionStatus(ConnectionState.CONNECTED)
LOST = ConnectionStatus(ConnectionState.RECONNECTING, "Connection lost: no broker connection")


def test_a_short_reconnect_without_positions_sends_nothing() -> None:
    found, sent, timers = watcher()
    found.on_connection(UP)
    found.on_connection(LOST)
    assert sent == [] and timers.waiting[0][0] == 60.0
    found.on_connection(UP)  # back after 11 s, as at the Sunday open
    timers.fire()
    assert sent == []


def test_a_connection_that_stays_lost_is_told_after_a_minute() -> None:
    found, sent, timers = watcher()
    found.on_connection(UP)
    found.on_connection(LOST)
    timers.fire()
    assert len(sent) == 1 and "Still not back after 60 s" in sent[0]


def test_a_lost_connection_with_open_positions_is_told_at_once() -> None:
    found, sent, timers = watcher()
    found.on_connection(UP)
    found.on_connection(ConnectionStatus(ConnectionState.RECONNECTING, "lost", open_positions=1))
    assert len(sent) == 1 and "1 open position" in sent[0]
    assert timers.waiting == []


class FakeDb:
    def __init__(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            "CREATE TABLE signals (bar_time TEXT, state TEXT, reject_reason TEXT);"
            "CREATE TABLE app_logs (time TEXT, level TEXT, message TEXT);"
            "CREATE TABLE health_checks (time TEXT, name TEXT, status TEXT);"
            "CREATE TABLE mt5_requests (created_at TEXT, retcode INTEGER);",
        )

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(sql, params))

    def scalar(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        row = self.connection.execute(sql, params).fetchone()
        return None if row is None else row[0]


class FakeStore:
    def __init__(self) -> None:
        self.db = FakeDb()


def test_the_weekly_report_lists_only_warnings_and_problems() -> None:
    store = FakeStore()
    period = week_period(date(2026, 9, 28))
    moment = iso_time(period.start + 3600)
    rows = [
        ("mt5_connected", "critical"),
        ("disk_space", "warning"),
        ("quotes_fresh", "unknown"),
        ("latency", "ok"),
    ]
    for name, status in rows:
        store.db.connection.execute(
            "INSERT INTO health_checks VALUES (?, ?, ?)",
            (moment, name, status),
        )
    found = ReportRepository(cast(Store, store)).inputs(period)
    assert sorted(found["health_issues"]) == ["disk_space: warning", "mt5_connected: critical"]


def test_ai_requests_name_the_app_and_explain_a_cloudflare_block() -> None:
    seen: dict[str, str] = {}

    def transport(url: str, headers: Mapping[str, str], body: bytes, timeout: float) -> bytes:
        seen.update(headers)
        return b'{"choices": [{"message": {"content": "ok"}}]}'

    settings = LlmSettings(enabled=True, base_url="https://api.example.com/v1")
    answer = ask(settings, "test-key", [{"role": "user", "content": "hi"}], transport=transport)
    assert answer.text == "ok"
    assert seen["User-Agent"].startswith("MT5TradingWorkstation/")
    blocked = '{"type":"https://developers.cloudflare.com/error-1010/","status":403}'
    assert "firewall (Cloudflare)" in http_error_text(403, blocked)
    assert http_error_text(401, "bad key").startswith("HTTP 401: The API key was refused")
    assert http_error_text(500, "oops") == "HTTP 500: oops"
