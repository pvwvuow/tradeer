"""The Telegram signals tab says which channels are on standby (phase 22k, 0.50.1)."""

from __future__ import annotations

from types import SimpleNamespace

from pytestqt.qtbot import QtBot

from app.channels.standby import StandbyBook
from app.ui.signal_inbox import SignalInbox
from app.ui.standby_bar import TITLE_FA, StandbyBar, left, standby_lines


def fake_feed(book: StandbyBook, now: float) -> SimpleNamespace:
    def source(channel_id: int) -> SimpleNamespace | None:
        return SimpleNamespace(title="Gold Room") if channel_id == 1 else None

    return SimpleNamespace(
        now=lambda: now,
        standby=book,
        repository=SimpleNamespace(source=source),
    )


def test_the_time_left_reads_as_minutes_and_seconds() -> None:
    assert left(500) == "8:20" and left(59.9) == "0:59" and left(-5) == "0:00"


def test_a_line_per_channel_on_standby() -> None:
    book = StandbyBook()
    book.open(1, 1000.0, "XAUUSD")
    book.open(2, 1000.0)  # a channel the repository no longer knows
    assert standby_lines(fake_feed(book, 1100.0)) == ["Gold Room (XAUUSD) 8:20", "2 8:20"]
    assert standby_lines(fake_feed(book, 1600.0)) == []  # the time is up


def test_the_bar_shows_and_hides_with_the_standby(qtbot: QtBot) -> None:
    book = StandbyBook()
    book.open(1, 1000.0, "XAUUSD")
    feed = fake_feed(book, 1100.0)
    inbox = SignalInbox()
    qtbot.addWidget(inbox)
    bar = StandbyBar(lambda: standby_lines(feed))
    assert bar.attach(inbox)
    layout = inbox.layout()
    assert layout is not None and layout.indexOf(bar.label) == 1
    assert bar.refresh() == "Standby: Gold Room (XAUUSD) 8:20" and not bar.label.isHidden()
    book.close(1)
    assert bar.refresh() == "" and bar.label.isHidden()
    persian = StandbyBar(lambda: ["Gold Room 1:00"], fa=True)
    assert persian.refresh().startswith(TITLE_FA)
    bar.stop()
    persian.stop()
