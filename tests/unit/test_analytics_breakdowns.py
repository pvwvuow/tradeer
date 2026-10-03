"""Breakdowns, chart data, behavior, projection, comparisons and CSV (spec C11)."""

import csv
import io

import pytest

from app.analytics import behavior, charts
from app.analytics.breakdowns import KEYS, breakdown, holding_bucket, label, probability_bucket
from app.analytics.compare import column, periods, rows, split
from app.analytics.export import groups_csv, trades_csv
from app.analytics.projection import project
from app.analytics.stats import compute_stats
from app.analytics.trades import TradeFilter
from tests.unit.analytics_helpers import DAY, HOUR, START, sample, trade


def test_breakdowns_group_by_every_key() -> None:
    trades = [
        trade(0, 100, symbol="EURUSD", source="bot", probability=0.62),
        trade(1, -50, symbol="XAUUSD", source="manual", probability=None),
        trade(2, 30, symbol="EURUSD", source="manual", probability=0.65),
    ]
    by_symbol = {g.key: g for g in breakdown(trades, "Symbol")}
    assert by_symbol["EURUSD"].trades == 2 and by_symbol["EURUSD"].net_profit == 130.0
    assert by_symbol["XAUUSD"].profit_factor == 0.0
    by_source = {g.key: g for g in breakdown(trades, "Bot or manual")}
    assert by_source["manual"].win_rate == pytest.approx(0.5)
    by_probability = {g.key: g.trades for g in breakdown(trades, "Probability")}
    assert by_probability == {"60-70%": 2, "unknown": 1}
    for key in KEYS:
        assert sum(g.trades for g in breakdown(trades, key)) == 3


def test_bucket_labels() -> None:
    assert label(holding_bucket(10 * 60)) == "under 15 min"
    assert label(holding_bucket(2 * HOUR)) == "1 to 4 hours"
    assert label(holding_bucket(9 * DAY)) == "over 7 days"
    assert probability_bucket(0.29) == "0-30%" and probability_bucket(0.71) == "70-100%"


def test_monthly_returns_compound_on_the_month_start_balance() -> None:
    october = trade(0, 100)
    november = trade(24 * 31, 110)
    found = charts.monthly_returns([october, november], 1000.0)
    assert found == {"2026-10": pytest.approx(10.0), "2026-11": pytest.approx(10.0)}


def test_the_calendar_and_the_r_distribution() -> None:
    days = charts.pl_calendar([*sample(), trade(30, 40)])
    assert days["2026-10-01"].net_profit == 125.0 and days["2026-10-01"].trades == 5
    assert days["2026-10-02"].wins == 1
    bins = dict(charts.r_distribution(sample()))
    assert bins == {-1.0: 2, -0.5: 1, 0.0: 0, 0.5: 0, 1.0: 0, 1.5: 0, 2.0: 1, 2.5: 0, 3.0: 1}


def test_excursions_find_profit_left_and_losers_that_were_ahead() -> None:
    trades = [
        trade(0, 100, r=2.0, mfe_r=3.0, mae_r=-0.2),
        trade(1, -50, r=-1.0, mfe_r=1.2, mae_r=-1.0),
        trade(2, 50, r=1.0, mfe_r=1.0, mae_r=-0.9),
    ]
    summary = charts.excursions(trades)
    assert summary.left_on_table_r == pytest.approx(0.5)
    assert summary.losers_were_ahead == 1 and summary.winners_near_stop == 1


def test_costs_as_a_share_of_the_gross_profit() -> None:
    trades = [
        trade(0, 90, profit=100, commission=-7, swap=-3),
        trade(1, -55, profit=-50, commission=-5, spread_r=0.1, slippage=2.0),
    ]
    costs = charts.cost_analysis(trades)
    assert costs.total == pytest.approx(15.0)
    assert costs.percent_of_gross == pytest.approx(15.0)
    assert costs.spread_r == pytest.approx(0.1) and costs.slippage == pytest.approx(2.0)


def test_behavior_flags_revenge_trades_overtrading_and_lots() -> None:
    manual = [trade(i, 10 if i % 3 else -20, source="manual", volume=0.1) for i in range(12)]
    opened = START + 3 * HOUR + 1900
    manual.append(trade(4, 30, id="r", source="manual", volume=0.5, open_time=opened))
    findings = {f.name: f for f in behavior.behavior_report(manual)}
    assert findings["Revenge trading"].flagged and findings["Revenge trading"].count == 1
    assert findings["Overtrading"].flagged is False  # one day only
    assert findings["Lot-size inconsistency"].flagged
    news = [behavior.NewsEvent(START + 2 * HOUR + 600, "USD", "high")]
    assert behavior.news_trading(manual, news).count == 1
    assert behavior.behavior_report(manual[:3])[0].name == "Manual trades"


def test_holding_losers_longer() -> None:
    trades = [
        trade(0, 10, close_time=START + 600),
        trade(1, -10, close_time=START + HOUR + 3600),
    ]
    finding = behavior.holding_losers(trades)
    assert finding.flagged and "6.0x" in finding.detail


def test_the_projection_and_risk_of_ruin() -> None:
    trades = [trade(i, 20 if i % 2 else -10) for i in range(30)]
    found = project(trades, 1000.0, horizon=50, runs=200)
    assert found is not None and len(found.median) == 50
    assert found.low[-1] <= found.median[-1] <= found.high[-1]
    assert found.median[-1] > 1150.0  # it made +150 on 30 trades and keeps that edge
    assert 0.0 <= found.ruin.risk_of_ruin <= 1.0
    assert project(trades[:5], 1000.0) is None


def test_comparisons_side_by_side() -> None:
    trades = [trade(0, 100, mode="paper"), trade(1, -50, mode="live"), trade(2, 80, mode="live")]
    columns = split(trades, "mode")
    assert [c.name for c in columns] == ["live", "paper"]
    table = {row[0]: row for row in rows(columns)}
    assert table["Net profit"][1:] == ["30.00", "100.00", "+70.00"]
    first = periods(trades, (START, START + HOUR), (START + HOUR, START + DAY))
    assert first[0].values["trades"] == 1.0 and first[1].values["trades"] == 2.0
    assert column("x", compute_stats([])).values["profit_factor"] is None


def test_filters_and_the_csv_export() -> None:
    trades = [trade(0, 100, source="manual"), trade(1, -50, symbol="XAUUSD"), trade(2, 5)]
    assert len(TradeFilter(symbol="EURUSD").apply(trades)) == 2
    assert len(TradeFilter(source="manual").apply(trades)) == 1
    assert len(TradeFilter(start=START + HOUR).apply(trades)) == 2
    text = trades_csv(trades)
    parsed = list(csv.DictReader(io.StringIO(text)))
    assert len(parsed) == 3 and parsed[0]["open_time"] == "2026-10-01 00:00:00"
    assert parsed[1]["net_profit"] == "-50"
    grouped = groups_csv("Symbol", breakdown(trades, "Holding time"))
    assert "15 to 60 min" in grouped
