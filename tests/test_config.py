from app.domain.config import TradingDefaults


def test_defaults_are_conservative_and_paper_first() -> None:
    settings = TradingDefaults()
    assert settings.mode == "paper"
    assert settings.risk_per_trade_percent == 0.5
    assert settings.max_open_trades == 3
    assert settings.symbols == ["EURUSD", "GBPUSD", "XAUUSD"]
