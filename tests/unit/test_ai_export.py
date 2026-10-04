"""The export for an outside AI (spec C13): the files, the prompt, the sections and the
parameter schemas the answer must keep."""

import csv
import io
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.analytics.ai_export import (
    CSV_NAME,
    JSON_NAME,
    PROMPT,
    REPORT_NAME,
    ExportData,
    build_report,
    calibration,
    rejected_counts,
    select_trades,
    trades_json,
    write_export,
)
from app.analytics.trades import TradeRecord
from app.core.strategy_settings import StrategySettings
from app.domain.modes import OperatingMode

NOW = 1_790_000_000.0
DAY = 86_400


def trade(number: int, **changes: Any) -> TradeRecord:
    won = number % 3 != 0
    record = TradeRecord(
        id=f"t{number}",
        account="acc",
        mode="paper",
        source="bot",
        symbol="EURUSD",
        direction="buy",
        volume=0.1,
        open_time=NOW - (number + 1) * DAY,
        close_time=NOW - (number + 1) * DAY + 3600,
        open_price=1.1,
        close_price=1.101 if won else 1.099,
        profit=20.0 if won else -10.0,
        commission=-0.7,
        swap=0.0,
        fee=0.0,
        net_profit=19.3 if won else -10.7,
        r_multiple=1.9 if won else -1.07,
        risk_money=10.0,
        mfe_r=2.0 if won else 0.3,
        mae_r=-0.4 if won else -1.0,
        probability=0.55 if won else 0.45,
        session="London",
        strategy="trend_pullback",
        config="cfg1",
        signal_id=f"s{number}",
        exit_reason="tp" if won else "sl",
    )
    return replace(record, **changes)


TRADES = [trade(number) for number in range(12)]


def data(**changes: Any) -> ExportData:
    base = ExportData(
        trades=TRADES,
        settings=StrategySettings(),
        mode=OperatingMode.PAPER,
        start_balance=10_000.0,
        currency="USD",
        backtests=[("EURUSD trend_pullback", {"trades": 80, "expectancy_r": 0.21})],
        rejected=[("spread too wide", 7), ("news blackout", 2)],
    )
    return replace(base, **changes)


class FakeDb:
    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        self.sql = ""

    def query(self, sql: str, parameters: Any = ()) -> list[dict[str, str]]:
        self.sql = sql
        return [{"reject_reason": reason} for reason in self.reasons]


def test_the_report_starts_with_the_prompt_and_has_every_section() -> None:
    text = build_report(data(), NOW)
    assert text.startswith(PROMPT)
    for heading in (
        "## Data",
        "## Statistics",
        "## Breakdowns",
        "### By Session",
        "## Probability calibration",
        "## Best and worst moves (MFE/MAE)",
        "## Costs",
        "## Rejected signals",
        "## Live and paper vs backtest",
        "## Strategy parameters",
        "## Not included yet",
    ):
        assert heading in text, heading
    assert "- Closed trades: 12" in text
    assert "- spread too wide: 7" in text
    assert "Backtest EURUSD trend_pullback: 80 trades, 0.210 R per trade" in text
    assert "### trend_pullback" in text and "### london_breakout" in text
    assert '"min_adx_h1"' in text and "Counterfactuals" in text
    assert '{"changes": [{"strategy": "trend_pullback"' in text


def test_an_empty_export_still_explains_itself() -> None:
    text = build_report(data(trades=[], rejected=[], backtests=[]), NOW)
    assert "No closed trades." in text
    assert "No trade has a predicted win probability yet." in text
    assert "None recorded." in text and "- No saved backtest." in text


def test_calibration_compares_predicted_and_actual() -> None:
    rows = calibration(TRADES)
    assert [row[0] for row in rows] == ["40-50%", "50-60%"]
    low, high = rows
    assert low[1] == 4 and abs(low[2] - 0.45) < 1e-9 and low[3] == 0.0
    assert high[1] == 8 and abs(high[2] - 0.55) < 1e-9 and high[3] == 1.0
    assert calibration([trade(1, probability=None)]) == []


def test_the_three_files_are_written(tmp_path: Path) -> None:
    paths = write_export(tmp_path / "ai", data(), NOW)
    assert [path.name for path in paths] == [CSV_NAME, JSON_NAME, REPORT_NAME]
    rows = list(csv.reader(io.StringIO(paths[0].read_text(encoding="utf-8"))))
    assert rows[0][0] == "id" and len(rows) == 13
    records = json.loads(paths[1].read_text(encoding="utf-8"))
    assert len(records) == 12 and records[0]["id"] == "t0"
    assert records[0]["open_time_utc"] and records[0]["close_time_utc"]
    assert paths[2].read_text(encoding="utf-8").startswith("# Trading review")


def test_trades_json_is_json_safe() -> None:
    found = json.loads(trades_json([trade(1, r_multiple=float("nan"))]))
    assert found[0]["r_multiple"] is None


def test_select_trades_by_days_strategy_and_mode() -> None:
    mixed = [*TRADES, trade(20, strategy="london_breakout", mode="live")]
    assert len(select_trades(mixed, NOW)) == 13
    assert len(select_trades(mixed, NOW, days=5)) == 5
    assert [t.id for t in select_trades(mixed, NOW, strategy="london_breakout")] == ["t20"]
    assert [t.id for t in select_trades(mixed, NOW, mode="live")] == ["t20"]


def test_rejected_reasons_are_counted_by_their_head() -> None:
    db = FakeDb(["spread: 3.1 > 2.0", "spread: 2.5 > 2.0", "news blackout", ""])
    assert rejected_counts(db) == [("spread", 2), ("news blackout", 1), ("unknown", 1)]
    assert "reject_reason" in db.sql
