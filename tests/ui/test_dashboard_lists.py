"""The Dashboard lists (8 October 2026): every signal and position, ten rows a page."""

from dataclasses import replace

from pytestqt.qtbot import QtBot

from app.domain.modes import OperatingMode
from app.domain.signals import Direction, SignalRecord, SignalState
from app.engine.execution import ExecutionSnapshot, PositionView
from app.engine.signal_pipeline import SignalsSnapshot
from app.ui.dashboard_page import (
    SIGNAL_VIEWS,
    DashboardContext,
    DashboardPage,
    ordered_positions,
    position_row,
    signal_row,
    signal_view,
    state_text,
    strategy_title,
)
from app.ui.paged_table import NOTHING, PagedTable, page_count, page_text
from tests.unit.signal_helpers import MORNING, make_record, make_signal


def record(index: int, state: SignalState, reason: str = "") -> SignalRecord:
    signal = make_signal(
        id=f"signal-{index}",
        created_at=float(MORNING + index * 900),
        direction=Direction.SHORT if index % 2 else Direction.LONG,
        entry=1.10000,
        sl=1.10100 if index % 2 else 1.09900,
        tp=1.09800 if index % 2 else 1.10200,
    )
    if state is SignalState.FILTERED_OUT:
        signal = signal.with_state(state, float(MORNING), reason)
        return replace(make_record(signal), reject_reason=reason)
    if state is SignalState.PENDING_APPROVAL:
        signal = signal.with_state(state, float(MORNING), "all filters passed")
    return make_record(signal)


def snapshot(count: int) -> SignalsSnapshot:
    records = [
        record(i, SignalState.FILTERED_OUT if i % 3 else SignalState.PENDING_APPROVAL, "session")
        for i in range(count)
    ]
    records.sort(key=lambda item: item.signal.created_at, reverse=True)
    return SignalsSnapshot(signals=tuple(records))


def view(ticket: int, symbol: str, *, pending: bool = False) -> PositionView:
    return PositionView(
        "live",
        ticket,
        symbol,
        "short",
        0.05,
        1.32124,
        1.32189,
        1.31994,
        None if pending else -3.2,
        "trend_pullback",
        pending,
    )


def test_page_count_and_text() -> None:
    assert page_count(0, 10) == 1 and page_count(10, 10) == 1 and page_count(11, 10) == 2
    assert page_text(0, 0, 10) == NOTHING
    assert page_text(1, 37, 10) == "11-20 of 37 \u00b7 page 2 of 4"
    assert page_text(3, 37, 10) == "31-37 of 37 \u00b7 page 4 of 4"


def test_paged_table_moves_between_pages_and_keeps_its_place(qtbot: QtBot) -> None:
    table = PagedTable(("A", "B"))
    qtbot.addWidget(table)
    table.set_rows([[str(i), f"row {i}"] for i in range(25)])
    assert table.table.rowCount() == 10 and table.pages == 3
    assert table.previous_button.isHidden() is False
    assert table.previous_button.isEnabled() is False
    table.next_page()
    table.next_page()
    assert table.page == 2 and table.table.rowCount() == 5
    assert table.table.item(0, 0).text() == "20"
    table.next_page()
    assert table.page == 2 and not table.next_button.isEnabled()
    # A refresh with the same list keeps the page; a shorter list moves back to its last page.
    table.set_rows([[str(i), f"row {i}"] for i in range(25)])
    assert table.page == 2
    table.set_rows([[str(i), f"row {i}"] for i in range(8)])
    assert table.page == 0 and table.table.rowCount() == 8
    assert table.previous_button.isHidden() and table.next_button.isHidden()
    # Always tall enough for a full page: rows are never squeezed.
    assert table.table.minimumHeight() >= 8 * 30


def test_every_signal_is_listed_paged_and_filtered(qtbot: QtBot) -> None:
    signals = snapshot(23)
    context = DashboardContext(signals=lambda: signals)
    page = DashboardPage(context)
    qtbot.addWidget(page)
    page.timer.stop()
    page.refresh(now=float(MORNING))
    assert page.signals.rowCount() == 10 and page.signals_list.total == 23
    assert page.signals_title.text() == "LATEST SIGNALS (23 of 23)"
    newest = page.signals.item(0, 0).text()
    page.signals_list.next_page()
    page.refresh(now=float(MORNING))
    assert page.signals_list.page == 1, "the two-second refresh keeps the page"
    assert page.signals.item(0, 0).text() != newest
    page.signal_filter.setCurrentText("Not traded")
    assert page.signals_list.page == 0
    states = {page.signals.item(row, 7).text() for row in range(page.signals.rowCount())}
    assert states == {"filtered out: session"}
    page.signal_filter.setCurrentText("Waiting for approval")
    assert page.signals_list.total == 8
    assert page.signals_title.text() == "LATEST SIGNALS (8 of 23)"


def test_signal_rows_say_strategy_side_prices_and_why() -> None:
    row = signal_row(record(1, SignalState.FILTERED_OUT, "trading session"))
    assert row[1] == strategy_title("trend_pullback") != "trend_pullback"
    assert row[3] == "Sell" and row[4:7] == ["1.10000", "1.10100", "1.09800"]
    assert row[7] == "filtered out: trading session"
    waiting = state_text(record(0, SignalState.PENDING_APPROVAL))
    assert waiting == "pending approval: all filters passed"
    assert signal_view(SignalState.CLOSED) == "Traded" and SIGNAL_VIEWS[0] == "All signals"
    assert signal_view(SignalState.EXPIRED) == "Not traded"


def test_positions_list_open_first_and_pending_orders_plainly(qtbot: QtBot) -> None:
    views = (view(3, "XAUUSD", pending=True), view(2, "GBPUSD"), view(1, "EURUSD"))
    assert [v.ticket for v in ordered_positions(views)] == [1, 2, 3]
    pending = position_row(views[0])
    assert pending[2] == "Sell (pending order)" and pending[7] == "-"
    assert position_row(views[1])[7] == "-3.20"
    context = DashboardContext(execution=lambda: ExecutionSnapshot(OperatingMode.AUTO, views))
    page = DashboardPage(context)
    qtbot.addWidget(page)
    page.timer.stop()
    page.refresh(now=float(MORNING))
    assert page.positions.rowCount() == 3
    assert page.positions.item(0, 0).text() == "EURUSD"
    assert page.positions_title.text() == "OPEN POSITIONS (2 open, 1 pending)"
    assert strategy_title("") == "-"
