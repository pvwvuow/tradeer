"""Risk limits, currency exposure and the persisted limit state (spec C6)."""

import json

from app.domain.signals import Direction
from app.risk.exposure import ExposureItem, as_percent, currency_exposure, exposure_text
from app.risk.limits import AccountPicture, Candidate, OpenPosition, check_trade, failed, usage
from app.risk.limits_state import (
    DAILY_LIMIT,
    DD_LIMIT,
    MANUAL_STOP,
    STATE_KEY,
    AccountMoney,
    LimitsState,
    LimitsStateStore,
    advance,
    re_enable,
    stop_trading,
)
from app.risk.settings import RiskSettings
from app.storage.repositories import Store
from tests.unit.storage_helpers import temporary_store

DAY = "2026-09-30"
NOW = 1_790_000_000.0
SETTINGS = RiskSettings()


def money(equity: float = 10_000.0, **changes: object) -> AccountMoney:
    values: dict[str, object] = {
        "day": DAY,
        "balance": 10_000.0,
        "equity": equity,
        "realized_today": 0.0,
        "deposits_today": 0.0,
    }
    values.update(changes)
    return AccountMoney(**values)  # type: ignore[arg-type]


def position(
    symbol: str = "EURUSD",
    direction: Direction = Direction.LONG,
    **changes: object,
) -> OpenPosition:
    values: dict[str, object] = {
        "ticket": 1,
        "symbol": symbol,
        "direction": direction,
        "volume": 0.5,
        "price_open": 1.1,
        "sl": 1.099,
        "profit": 0.0,
        "magic": 26_070_001,
        "risk_money": 50.0,
        "base": symbol[:3],
        "quote": symbol[3:6],
        "strategy": "trend_pullback",
    }
    values.update(changes)
    return OpenPosition(**values)  # type: ignore[arg-type]


def picture(
    *positions: OpenPosition,
    equity: float = 10_000.0,
    **changes: object,
) -> AccountPicture:
    values: dict[str, object] = {
        "currency": "USD",
        "balance": 10_000.0,
        "equity": equity,
        "margin": 0.0,
        "margin_free": equity,
        "margin_level": 0.0,
        "positions": positions,
        "money": money(equity),
        "bot_entries_today": 0,
        "manual_entries_today": 0,
        "read_at": NOW,
    }
    values.update(changes)
    return AccountPicture(**values)  # type: ignore[arg-type]


def candidate(symbol: str = "GBPUSD", **changes: object) -> Candidate:
    values: dict[str, object] = {
        "symbol": symbol,
        "strategy": "london_breakout",
        "direction": Direction.LONG,
        "risk_money": 50.0,
        "base": symbol[:3],
        "quote": symbol[3:6],
        "stop_points": 150.0,
        "stops_level": 10,
        "margin_required": 500.0,
    }
    values.update(changes)
    return Candidate(**values)  # type: ignore[arg-type]


def started() -> LimitsState:
    state, _ = advance(LimitsState(), money(), SETTINGS, NOW)
    return state


def names(checks: object) -> list[str]:
    return [check.name for check in failed(checks)]  # type: ignore[arg-type]


def test_long_eurusd_and_gbpusd_are_twice_short_usd() -> None:
    totals = currency_exposure(
        [
            ExposureItem("EUR", "USD", Direction.LONG, 50.0),
            ExposureItem("GBP", "USD", Direction.LONG, 50.0),
            ExposureItem("XAU", "USD", Direction.SHORT, 50.0),
        ],
    )
    assert totals == {"EUR": 50.0, "GBP": 50.0, "USD": -50.0, "XAU": -50.0}
    assert as_percent({"USD": -100.0}, 10_000.0) == {"USD": -1.0}
    assert exposure_text({"USD": -1.0, "EUR": 0.5}) == "short USD 1.00%, long EUR 0.50%"


def test_a_clean_account_passes_every_check() -> None:
    checks = check_trade(candidate(), picture(), started(), SETTINGS)
    assert names(checks) == []
    assert {"daily loss", "drawdown", "total open risk", "currency exposure", "margin"} <= {
        check.name for check in checks
    }


def test_a_third_usd_short_breaks_the_currency_exposure_limit() -> None:
    open_ = (position("EURUSD"), position("AUDUSD", ticket=2, strategy="", magic=0))
    checks = check_trade(candidate("GBPUSD"), picture(*open_), started(), SETTINGS)
    assert names(checks) == ["currency exposure"]
    blocked = failed(checks)[0]
    assert blocked.event == "exposure_block" and blocked.value == 1.5


def test_manual_trades_count_only_when_the_setting_says_so() -> None:
    manual = tuple(position(f"EUR{q}", ticket=i, strategy="", magic=0) for i, q in enumerate("AB"))
    full = picture(*manual, *[position("NZDCAD", ticket=9, strategy="", magic=0)])
    assert "open trades" in names(check_trade(candidate("CHFJPY"), full, started(), SETTINGS))
    ignore = SETTINGS.model_copy(update={"count_manual_trades": False})
    assert names(check_trade(candidate("CHFJPY"), full, started(), ignore)) == []


def test_open_risk_per_symbol_strategy_and_day_limits() -> None:
    state = started()
    big = candidate("EURUSD", risk_money=120.0, strategy="trend_pullback")
    found = names(check_trade(big, picture(position("EURUSD")), state, SETTINGS))
    assert found == ["open trades on the symbol", "total open risk", "currency exposure"]
    busy = picture(bot_entries_today=6)
    assert names(check_trade(candidate(), busy, state, SETTINGS)) == ["trades today"]
    two = picture(position("EURJPY"), position("AUDNZD", ticket=2))
    trend = candidate("CADCHF", strategy="trend_pullback")
    assert names(check_trade(trend, two, state, SETTINGS)) == ["open trades of the strategy"]


def test_a_position_without_stop_loss_counts_its_current_loss() -> None:
    naked = position(sl=0.0, risk_money=None, profit=-80.0)
    assert naked.counted_risk == 80.0
    assert position(risk_money=None, profit=25.0).counted_risk == 0.0


def test_stops_level_and_margin_floor() -> None:
    state = started()
    near = candidate(stop_points=5.0, stops_level=10)
    assert names(check_trade(near, picture(), state, SETTINGS)) == ["stop distance"]
    heavy = candidate(margin_required=4_000.0)  # margin level 250% < 300%
    assert names(check_trade(heavy, picture(), state, SETTINGS)) == ["margin"]
    unknown = candidate(margin_required=None)
    blocked = failed(check_trade(unknown, picture(), state, SETTINGS))
    assert [check.event for check in blocked] == ["margin_block"]


def test_the_daily_loss_stops_entries_until_the_next_trading_day() -> None:
    state = started()
    assert state.day == DAY and state.day_start_equity == 10_000.0
    state, events = advance(state, money(9_790.0), SETTINGS, NOW + 60)
    assert state.halted == DAILY_LIMIT and [e.type for e in events] == [DAILY_LIMIT]
    down = picture(equity=9_790.0)
    assert "trading allowed" in names(check_trade(candidate(), down, state, SETTINGS))
    again, events = advance(state, money(9_780.0), SETTINGS, NOW + 120)
    assert again.halted == DAILY_LIMIT and events == []
    tomorrow, events = advance(again, money(9_780.0, day="2026-10-01"), SETTINGS, NOW + 300)
    assert tomorrow.halted == "" and tomorrow.day_start_equity == 9_780.0
    assert not tomorrow.day_start_estimated  # the app saw the rollover


def test_deposits_and_withdrawals_are_not_profit_or_loss() -> None:
    state = started()
    state, _ = advance(state, money(9_000.0, deposits_today=-1_000.0), SETTINGS, NOW + 60)
    after = money(9_000.0, deposits_today=-1_000.0)
    assert state.halted == "" and state.daily_loss_percent(after) == 0


def test_the_drawdown_stop_stays_until_the_user_re_enables() -> None:
    state, _ = advance(started(), money(10_500.0), SETTINGS, NOW + 60)
    assert state.high_water_mark == 10_500.0
    tomorrow = money(9_600.0, day="2026-10-01", balance=9_600.0)
    state, events = advance(state, tomorrow, SETTINGS, NOW + 86_400)
    assert state.halted == DD_LIMIT and events[0].type == DD_LIMIT  # 8.57% from 10,500
    later, _ = advance(state, money(9_700.0, day="2026-10-02"), SETTINGS, NOW + 2 * 86_400)
    assert later.halted == DD_LIMIT
    enabled = re_enable(later, 9_700.0, NOW + 2 * 86_400)
    assert enabled.halted == "" and enabled.high_water_mark == 9_700.0


def test_a_static_drawdown_measures_from_the_first_equity() -> None:
    static = SETTINGS.model_copy(update={"drawdown_mode": "static"})
    state, _ = advance(LimitsState(), money(), static, NOW)
    state, _ = advance(state, money(12_000.0), static, NOW + 60)
    assert state.drawdown_percent(money(11_000.0), static) == 0.0
    assert state.drawdown_percent(money(11_000.0), SETTINGS) > 8.0


def test_a_first_start_mid_day_estimates_the_day_start_from_the_balance() -> None:
    mid_day = money(9_950.0, balance=9_900.0, realized_today=-100.0)
    state, _ = advance(LimitsState(), mid_day, SETTINGS, NOW)
    assert state.day_start_equity == 10_000.0 and state.day_start_estimated


def save_and_load(store: Store, state: LimitsState) -> LimitsState:
    LimitsStateStore(store).save("acc", state)
    return LimitsStateStore(store).load("acc")


def test_limit_state_survives_a_restart() -> None:
    with temporary_store() as store:
        state, _ = advance(started(), money(9_790.0), SETTINGS, NOW + 60)
        assert save_and_load(store, state) == state
        manual = stop_trading(state, "user", NOW)
        assert save_and_load(store, manual).halted == MANUAL_STOP
        assert LimitsStateStore(store).load("other") == LimitsState()


def test_a_damaged_saved_state_stops_trading_instead_of_resetting() -> None:
    with temporary_store() as store:
        logs: list[tuple[str, str]] = []
        for raw in ("{not json", json.dumps({"day_start_equity": "x"}), "[]"):
            store.set_state(STATE_KEY.format(account="acc"), raw)
            states = LimitsStateStore(store, lambda level, text: logs.append((level, text)))
            state = states.load("acc")
            assert state.halted == MANUAL_STOP and "could not be read" in state.halted_reason
        assert len(logs) == 3


def test_usage_reports_every_limit() -> None:
    state, _ = advance(started(), money(9_900.0), SETTINGS, NOW + 60)
    now = picture(position("EURUSD"), equity=9_900.0, money=money(9_900.0))
    found = usage(now, state, SETTINGS)
    assert round(found.daily_loss_percent, 2) == 1.0
    assert found.open_trades == 1 and round(found.open_risk_percent, 3) == round(50 / 99, 3)
    assert found.exposure_percent["USD"] < 0 and found.halted == ""
