"""The chart's live price (8 October 2026: the prices looked different from MT5): the bid
and ask lines follow the quote, and the time labels are broker time, as in MT5."""

from datetime import UTC, datetime

from pytestqt.qtbot import QtBot

from app.analysis.bars import Bars
from app.ui.chart import CandleChart, bar_moment, time_ticks
from tests.unit.strategy_helpers import analysis_at, london_days

HOUR = 3600


def test_the_bid_and_ask_follow_the_quote(qtbot: QtBot) -> None:
    m15 = london_days()
    analysis, _ = analysis_at(m15, len(m15))
    chart = CandleChart()
    qtbot.addWidget(chart)
    chart.set_analysis(analysis)
    before = chart.item_count
    assert "live bid" not in chart.info.text()
    chart.set_quote(1.10012, 1.10020, 1_790_751_600)
    assert chart.item_count == before + 1  # the bid and the ask line instead of the close
    assert "live bid 1.10012 ask 1.10020 at 07:00:00 broker time" in chart.info.text()
    chart.set_quote(1.10030, 1.10038, 1_790_751_602)
    assert chart.item_count == before + 1
    assert "live bid 1.10030" in chart.info.text()
    chart.set_analysis(analysis)  # a new evaluation of the same symbol keeps the quote
    assert chart.item_count == before + 1
    chart.set_quote(None, None)
    assert chart.item_count == before and "live bid" not in chart.info.text()


def test_the_time_labels_are_broker_time() -> None:
    server = [1_790_762_400, 1_790_762_400 + HOUR]  # 10:00 and 11:00 on the broker clock
    ones = [1.0, 1.0]
    bars = Bars.build(
        "EURUSD",
        "H1",
        time=[moment - 3 * HOUR for moment in server],
        open=ones,
        high=ones,
        low=ones,
        close=ones,
        server_time=server,
    )
    assert bar_moment(bars, 0) == datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
    assert time_ticks(bars, 0, 1, count=2) == [(0, "30 Sep 10:00"), (1, "30 Sep 11:00")]
