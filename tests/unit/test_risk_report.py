"""The Risk page's plain-language rows (spec F3 page 11)."""

from app.risk.limits import RiskUsage
from app.risk.limits_state import DAILY_LIMIT
from app.risk.report import exposure_rows, risk_in_words, status_text, usage_rows
from app.risk.settings import RiskSettings


def make_usage(**changes: object) -> RiskUsage:
    values: dict[str, object] = {
        "capital": 10_000.0,
        "currency": "USD",
        "daily_loss_percent": 0.4,
        "drawdown_percent": 1.25,
        "open_risk_percent": 0.5,
        "open_trades": 1,
        "trades_today": 2,
        "exposure_percent": {"USD": -0.5, "EUR": 0.5, "GBP": 0.001},
        "margin_level": 1818.0,
        "halted": "",
        "halted_reason": "",
        "day": "2026-09-30",
        "day_start_equity": 10_000.0,
        "day_start_estimated": True,
        "high_water_mark": 10_100.0,
    }
    values.update(changes)
    return RiskUsage(**values)  # type: ignore[arg-type]


def test_every_limit_has_a_row_with_its_allowed_value() -> None:
    rows = usage_rows(make_usage(), RiskSettings())
    assert [row[0] for row in rows] == [
        "Daily loss",
        "Drawdown",
        "Open risk",
        "Open trades",
        "Trades today",
        "Margin level",
    ]
    assert rows[0][1:3] == ("0.40%", "2%") and "estimated" in rows[0][3]
    assert rows[5][1] == "1,818%"


def test_status_and_plain_words() -> None:
    assert "Not connected" in status_text(None)
    assert status_text(make_usage()).startswith("Trading allowed")
    halted = make_usage(halted=DAILY_LIMIT, halted_reason="daily loss 2.1%")
    assert "Daily loss limit hit" in status_text(halted) and "2.1%" in status_text(halted)
    words = risk_in_words(make_usage(), RiskSettings())
    assert words == "Each new trade can lose about 50.00 USD at most."


def test_exposure_rows_skip_tiny_values() -> None:
    assert exposure_rows(make_usage()) == [("USD", "short 0.50%"), ("EUR", "long 0.50%")]
