from types import SimpleNamespace

from app.mt5 import api
from app.mt5.models import (
    AccountKind,
    AccountSnapshot,
    MarginMode,
    Quote,
    SymbolSpec,
    SymbolTradeMode,
    TerminalSnapshot,
    server_time_text,
)


def account(**changes: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "login": 51234567,
        "name": "Test Trader",
        "server": "Broker-Demo",
        "company": "Broker Ltd",
        "currency": "USD",
        "balance": 10_000.0,
        "equity": 10_050.0,
        "margin_free": 9_000.0,
        "leverage": 100,
        "trade_mode": api.ACCOUNT_TRADE_MODE_DEMO,
        "margin_mode": api.ACCOUNT_MARGIN_MODE_RETAIL_NETTING,
        "trade_allowed": True,
        "trade_expert": True,
        "margin_so_mode": api.ACCOUNT_STOPOUT_MODE_PERCENT,
        "margin_so_call": 100.0,
        "margin_so_so": 50.0,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_account_snapshot_detects_kind_margin_mode_and_read_only() -> None:
    snapshot = AccountSnapshot.from_mt5(account())
    assert snapshot.kind is AccountKind.DEMO
    assert snapshot.margin_mode is MarginMode.NETTING
    assert not snapshot.read_only
    assert snapshot.stop_out_text() == "margin call 100%, stop-out 50%"
    assert "51234567" in snapshot.summary()
    assert "1:100" in snapshot.summary()
    real = AccountSnapshot.from_mt5(account(trade_mode=api.ACCOUNT_TRADE_MODE_REAL))
    assert real.kind is AccountKind.REAL
    investor = AccountSnapshot.from_mt5(account(trade_allowed=False))
    assert investor.read_only
    money = AccountSnapshot.from_mt5(account(margin_so_mode=api.ACCOUNT_STOPOUT_MODE_MONEY))
    assert money.stop_out_text() == "margin call 100 USD, stop-out 50 USD"
    assert AccountSnapshot.from_mt5(account(trade_mode=9)).kind is AccountKind.UNKNOWN


def test_terminal_snapshot_reads_ping_in_milliseconds_and_build_from_version() -> None:
    info = SimpleNamespace(name="MT5", connected=True, trade_allowed=False, ping_last=35_000)
    snapshot = TerminalSnapshot.from_mt5(info, (500, 4755, "21 Feb 2025"))
    assert snapshot.ping_ms == 35.0
    assert snapshot.build == 4755
    assert snapshot.connected
    assert not snapshot.trade_allowed
    assert not snapshot.tradeapi_disabled


def test_symbol_spec_and_quotes() -> None:
    info = SimpleNamespace(
        name="XAUUSD.m",
        digits=2,
        point=0.01,
        trade_tick_size=0.01,
        trade_tick_value=1.0,
        trade_contract_size=100.0,
        volume_min=0.01,
        volume_max=50.0,
        volume_step=0.01,
        trade_stops_level=20,
        trade_freeze_level=0,
        filling_mode=2,
        trade_mode=api.SYMBOL_TRADE_MODE_CLOSEONLY,
        currency_margin="XAU",
        currency_profit="USD",
        visible=True,
    )
    spec = SymbolSpec.from_mt5(info)
    assert spec.trade_mode is SymbolTradeMode.CLOSE_ONLY
    assert (spec.contract_size, spec.stops_level, spec.currency_profit) == (100.0, 20, "USD")
    quote = Quote.from_mt5("XAUUSD.m", SimpleNamespace(bid=2385.4, ask=2385.65, time=0), 2)
    assert quote.valid
    assert quote.text() == "XAUUSD.m 2385.40 / 2385.65"
    assert not Quote("X", 0.0, 0.0, 0).valid
    assert server_time_text(0) == "1970-01-01 00:00:00 (server)"
