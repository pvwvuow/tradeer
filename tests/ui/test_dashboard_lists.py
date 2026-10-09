"""The Dashboard lists: every signal and position, ten rows a page (No Curve v2 rows)."""

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
    pipeline_steps,
    position_row,
    signal_row,
    signal_view,
    state_text,
    strategy_title,
)
from app.ui.paged_table import NOTHING, PagedTable, page_count, page_text
from app.ui.v2 import Cell, Column, DesignTable, Row
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
    table.set_rows([[str(i), f"row {i}"] for i in range(25)])
    assert table.page == 2
    table.set_rows([[str(i), f"row {i}"] for i in range(8)])
    assert table.page == 0 and table.table.rowCount() == 8
    assert table.previous_button.isHidden() and table.next_button.isHidden()
    assert table.table.minimumHeight() >= 8 * 30


def test_design_table_pages_and_keeps_its_place(qtbot: QtBot) -> None:
    table = DesignTable([Column("A"), Column("B", end=True)], "nothing")
    qtbot.addWidget(table)
    table.show()
    assert table.empty.isVisible() and not table.table.isVisible()
    rows = [Row((Cell(str(i)), Cell(f"r{i}", tag="warning")), key=str(i)) for i in range(25)]
    table.set_rows(rows)
    assert table.table.rowCount() == 10 and table.pages == 3 and table.pager.isVisible()
    table.next_page()
    table.next_page()
    assert table.page == 2 and table.table.rowCount() == 5 and table.text(0, 0) == "20"
    assert table.table.cellWidget(0, 1) is not None  # the tag is a widget
    table.set_rows([Row((Cell(str(i)), Cell("x")), key=str(i)) for i in range(8)])
    assert table.page == 0 and not table.pager.isVisible()
    clicked: list[str] = []
    table.row_clicked.connect(clicked.append)
    table.table.cellClicked.emit(3, 0)
    assert clicked == ["3"]


def test_every_signal_is_listed_paged_and_filtered(qtbot: QtBot) -> None:
    signals = snapshot(23)
    page = DashboardPage(DashboardContext(signals=lambda: signals))
    qtbot.addWidget(page)
    page.timer.stop()
    page.refresh(now=float(MORNING))
    assert page.signals.table.rowCount() == 10 and page.signals.total == 23
    assert page.signals_title.text() == "23/23"
    assert page.waiting_count == 8
    newest = page.signals.text(0, 0)
    page.signals.next_page()
    page.refresh(now=float(MORNING))
    assert page.signals.page == 1, "the two-second refresh keeps the page"
    assert page.signals.text(0, 0) != newest
    page.signal_filter.setCurrentIndex(SIGNAL_VIEWS.index("Not traded"))
    assert page.signals.page == 0
    results = {page.signals.text(row, 5) for row in range(page.signals.table.rowCount())}
    assert results == {"Filtered out"}
    page.signal_filter.setCurrentIndex(SIGNAL_VIEWS.index("Waiting for approval"))
    assert page.signals.total == 8 and page.signals_title.text() == "8/23"
    opened: list[str] = []
    page.go = opened.append
    page.signals.table.cellClicked.emit(0, 0)
    assert opened == ["signals"]


def test_signal_rows_say_strategy_side_prices_and_why() -> None:
    row = signal_row(record(1, SignalState.FILTERED_OUT, "trading session"))
    assert row[1] == strategy_title("trend_pullback") != "trend_pullback"
    assert row[3] == "Sell" and row[4:7] == ["1.10000", "1.10100", "1.09800"]
    assert row[7] == "filtered out: trading session"
    waiting = state_text(record(0, SignalState.PENDING_APPROVAL))
    assert waiting == "pending approval: all filters passed"
    assert signal_view(SignalState.CLOSED) == "Traded" and SIGNAL_VIEWS[0] == "All signals"
    assert signal_view(SignalState.EXPIRED) == "Not traded"
    assert pipeline_steps(record(2, SignalState.FILTERED_OUT, "spread 2.6 > 2.0")) == [
        1,
        1,
        -1,
        0,
        0,
    ]
    assert pipeline_steps(record(2, SignalState.FILTERED_OUT, "news: GBP in 25 min"))[3] == -1


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
    assert page.positions.total == 3 and page.positions.text(0, 0) == "EUR/USD"
    assert page.positions.text(0, 1) == "Sell" and page.positions.text(0, 4) == "\u2014"
    assert page.positions.text(0, 5) == "1.32189"
    assert page.positions.text(0, 6) == "\u2212$3.20"
    assert page.positions.text(2, 6) == "Pending order"
    assert page.positions_title.text() == "2 open, 1 pending"
    assert strategy_title("") == "-"
