import tempfile
from pathlib import Path

from app.calendar.csv_import import decode, parse_calendar_csv, parse_time
from app.calendar.exporter import (
    EXPORTER_FILE_NAME,
    CalendarFileWatcher,
    bundled_exporter,
    install_exporter,
)
from app.calendar.models import CalendarEvent, Impact, blocking_event, parse_impact, upcoming
from app.calendar.store import CalendarStore
from tests.unit.storage_helpers import temporary_store

NOW = 1_790_000_000
EXPORTER_CSV = (
    "time_utc,currency,importance,event,actual,forecast,previous,country\r\n"
    f'{NOW + 3600},USD,high,"CPI m/m","","0.3%","0.2%",US\r\n'
    f'{NOW + 7200},EUR,medium,"ZEW, Economic Sentiment","","","",EU\r\n'
    f'{NOW + 9000},JPY,none,"Bank Holiday","","","",JP\r\n'
)


def test_the_exporter_file_is_read() -> None:
    result = parse_calendar_csv(EXPORTER_CSV, source="mt5")
    assert result.errors == []
    assert [event.currency for event in result.events] == ["USD", "EUR", "JPY"]
    cpi = result.events[0]
    expected = (NOW + 3600, Impact.HIGH, "0.3%", "mt5")
    assert (cpi.time, cpi.impact, cpi.forecast, cpi.source) == expected
    assert result.events[1].title == "ZEW, Economic Sentiment"


def test_a_hand_made_csv_with_other_headers_and_bad_lines() -> None:
    text = (
        "Date_Time,Currency,Impact,Title\n"
        "2026-10-02 12:30,usd,High,Nonfarm Payrolls\n"
        "2026-10-02T14:00:00Z,EUR,3,ECB speech\n"
        "yesterday,USD,high,Broken time\n"
        "2026-10-02 15:00,GBP,huge,Broken impact\n"
    )
    result = parse_calendar_csv(text)
    assert [event.title for event in result.events] == ["Nonfarm Payrolls", "ECB speech"]
    assert result.events[0].currency == "USD"
    assert result.skipped == 2 and result.errors[0].startswith("line 4")
    assert "2 events read" in result.text()
    assert parse_calendar_csv("a,b\n1,2\n").errors[0].startswith("missing columns")
    assert parse_time("1790000000") == 1_790_000_000
    assert decode("time\n".encode("utf-16")) == "time\n"
    assert parse_impact("Moderate") is Impact.MEDIUM


def test_upcoming_and_blocking_events() -> None:
    events = parse_calendar_csv(EXPORTER_CSV).events
    soon = upcoming(events, NOW, hours=24)
    assert [event.currency for event in soon] == ["USD", "EUR"]
    assert upcoming(events, NOW, symbols=["XAUUSD"]) == [events[0]]
    assert blocking_event(events, "EURUSD", NOW) == events[0]
    assert blocking_event(events, "EURJPY", NOW) is None
    assert events[0].countdown(NOW) == "in 1h 00m"
    assert events[0].countdown(NOW + 1800) == "in 30 min"
    assert events[1].countdown(NOW) == "in 2h 00m"
    assert events[0].countdown(NOW + 4000) == "6 min ago"
    assert events[0].id == CalendarEvent(NOW + 3600, "usd", Impact.LOW, " CPI  M/M ").id


def test_events_are_stored_once_and_read_back() -> None:
    events = parse_calendar_csv(EXPORTER_CSV, source="mt5").events
    with temporary_store() as store:
        calendar = CalendarStore(store)
        assert calendar.save(events) == 3
        assert calendar.save(events) == 0
        updated = CalendarEvent(
            NOW + 3600,
            "USD",
            Impact.HIGH,
            "CPI m/m",
            actual="0.4%",
            source="mt5",
        )
        assert calendar.save([updated]) == 1
        found = calendar.recent_and_upcoming(NOW)
        titles = [event.title for event in found]
        assert titles == ["CPI m/m", "ZEW, Economic Sentiment", "Bank Holiday"]
        assert found[0].actual == "0.4%"
        assert store.count("calendar_events") == 3


def test_the_exporter_is_installed_and_its_file_watched() -> None:
    source = bundled_exporter()
    assert source.name == EXPORTER_FILE_NAME and source.is_file()
    text = source.read_text(encoding="utf-8")
    assert "#property service" in text and "CalendarValueHistory" in text
    assert "order" not in text.casefold().replace("border", "")
    with tempfile.TemporaryDirectory() as folder:
        target = install_exporter(folder)
        assert target == Path(folder) / "MQL5" / "Services" / EXPORTER_FILE_NAME
        assert target.read_text(encoding="utf-8") == text
        csv_path = Path(folder) / "tradeer_calendar.csv"
        watcher = CalendarFileWatcher(csv_path)
        assert watcher.check() is None
        csv_path.write_bytes(EXPORTER_CSV.encode("utf-8"))
        first = watcher.check()
        assert first is not None and len(first.events) == 3
        assert watcher.check() is None
        assert watcher.last_modified is not None
