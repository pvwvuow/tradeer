"""Risk limits for a new trade (spec C6). Pure: the account picture comes from one MT5 read.

Every check is a line of the decision trace with its value and threshold: trading stops,
daily loss, drawdown, open trades (total, per symbol, per strategy), trades today, total
open risk, currency exposure, the broker's stops level and the margin after the trade.
Manual trades (magic 0) and other EAs' trades count toward exposure, open risk and daily
trades when the setting says so; the bot never touches them.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.signals import Direction
from app.risk.exposure import ExposureItem, as_percent, currency_exposure, exposure_text
from app.risk.limits_state import (
    DAILY_LIMIT,
    DD_LIMIT,
    AccountMoney,
    LimitsState,
)
from app.risk.settings import RiskSettings

EXPOSURE_BLOCK = "exposure_block"
MARGIN_BLOCK = "margin_block"
LIMIT_BLOCK = "limit_block"


@dataclass(frozen=True)
class OpenPosition:
    ticket: int
    symbol: str
    direction: Direction
    volume: float
    price_open: float
    sl: float  # 0 = no stop loss
    profit: float  # floating, account currency
    magic: int
    risk_money: float | None  # loss at the SL from order_calc_profit; None without a SL
    base: str = ""
    quote: str = ""
    strategy: str = ""  # from the magic number; "" for manual trades and other EAs

    @property
    def manual(self) -> bool:
        """Not this app's trade: manual (magic 0) or another EA's."""
        return not self.strategy

    @property
    def counted_risk(self) -> float:
        """The money this position can still lose. Without a stop loss: its current loss."""
        if self.risk_money is not None and math.isfinite(self.risk_money):
            return max(0.0, self.risk_money)
        return max(0.0, -self.profit)


@dataclass(frozen=True)
class AccountPicture:
    """Account, open positions and today's deals, read from MT5 in one gateway call."""

    currency: str
    balance: float
    equity: float
    margin: float
    margin_free: float
    margin_level: float
    positions: tuple[OpenPosition, ...]
    money: AccountMoney
    bot_entries_today: int
    manual_entries_today: int
    read_at: float

    def capital(self, settings: RiskSettings) -> float:
        return self.equity if settings.capital_basis == "equity" else self.balance

    def counted(self, settings: RiskSettings) -> list[OpenPosition]:
        return [p for p in self.positions if settings.count_manual_trades or not p.manual]

    def entries_today(self, settings: RiskSettings) -> int:
        manual = self.manual_entries_today if settings.count_manual_trades else 0
        return self.bot_entries_today + manual


@dataclass(frozen=True)
class Candidate:
    """The new trade, already sized."""

    symbol: str
    strategy: str
    direction: Direction
    risk_money: float
    base: str
    quote: str
    stop_points: float  # distance entry -> SL in points
    stops_level: int  # broker minimum distance in points (0 = none)
    margin_required: float | None  # order_calc_margin for the lot; None if MT5 failed


@dataclass(frozen=True)
class LimitCheck:
    name: str
    passed: bool
    value: float | str | None
    threshold: float | str | None
    detail: str
    event: str = ""  # the risk_events type when this check blocks the trade


@dataclass(frozen=True)
class RiskUsage:
    """How much of each limit is used now: the Risk page and the status bar."""

    capital: float
    currency: str
    daily_loss_percent: float
    drawdown_percent: float
    open_risk_percent: float
    open_trades: int
    trades_today: int
    exposure_percent: dict[str, float]
    margin_level: float
    halted: str
    halted_reason: str
    day: str
    day_start_equity: float
    day_start_estimated: bool
    high_water_mark: float
    balance: float = math.nan
    equity: float = math.nan


def usage(picture: AccountPicture, state: LimitsState, settings: RiskSettings) -> RiskUsage:
    capital = picture.capital(settings)
    counted = picture.counted(settings)
    open_risk = sum(p.counted_risk for p in counted)
    exposure = currency_exposure(_items(counted))
    return RiskUsage(
        capital=capital,
        currency=picture.currency,
        daily_loss_percent=state.daily_loss_percent(picture.money),
        drawdown_percent=state.drawdown_percent(picture.money, settings),
        open_risk_percent=open_risk / capital * 100.0 if capital > 0 else 0.0,
        open_trades=len(counted),
        trades_today=picture.entries_today(settings),
        exposure_percent=as_percent(exposure, capital),
        margin_level=picture.margin_level,
        halted=state.halted,
        halted_reason=state.halted_reason,
        day=state.day,
        day_start_equity=state.day_start_equity,
        day_start_estimated=state.day_start_estimated,
        high_water_mark=state.high_water_mark,
        balance=picture.balance,
        equity=picture.equity,
    )


def _items(positions: Sequence[OpenPosition]) -> list[ExposureItem]:
    return [ExposureItem(p.base, p.quote, p.direction, p.counted_risk) for p in positions]


def check_trade(
    candidate: Candidate,
    picture: AccountPicture,
    state: LimitsState,
    settings: RiskSettings,
) -> list[LimitCheck]:
    checks: list[LimitCheck] = []
    capital = picture.capital(settings)
    counted = picture.counted(settings)
    money = picture.money

    if state.halted:
        event = state.halted if state.halted in (DAILY_LIMIT, DD_LIMIT) else LIMIT_BLOCK
        stopped = LimitCheck("trading allowed", False, state.halted, "", state.halted_reason, event)
        checks.append(stopped)
    else:
        checks.append(LimitCheck("trading allowed", True, "yes", "", "no limit stopped trading"))

    daily = state.daily_loss_percent(money)
    estimate = " (start of day estimated from the balance)" if state.day_start_estimated else ""
    checks.append(
        LimitCheck(
            "daily loss",
            daily < settings.max_daily_loss_percent,
            round(daily, 3),
            settings.max_daily_loss_percent,
            f"realized + floating since the day started at {state.day_start_equity:,.2f} "
            f"{picture.currency}{estimate}",
            DAILY_LIMIT,
        ),
    )
    drawdown = state.drawdown_percent(money, settings)
    basis = state.drawdown_basis(settings)
    checks.append(
        LimitCheck(
            "drawdown",
            drawdown < settings.max_total_drawdown_percent,
            round(drawdown, 3),
            settings.max_total_drawdown_percent,
            f"{settings.drawdown_mode} from {basis:,.2f} {picture.currency}",
            DD_LIMIT,
        ),
    )
    checks.append(
        LimitCheck(
            "open trades",
            len(counted) < settings.max_open_trades,
            len(counted),
            settings.max_open_trades,
            "manual trades included" if settings.count_manual_trades else "bot trades only",
            LIMIT_BLOCK,
        ),
    )
    on_symbol = sum(1 for p in counted if p.symbol == candidate.symbol)
    checks.append(
        LimitCheck(
            "open trades on the symbol",
            on_symbol < settings.max_open_per_symbol,
            on_symbol,
            settings.max_open_per_symbol,
            candidate.symbol,
            LIMIT_BLOCK,
        ),
    )
    of_strategy = sum(1 for p in picture.positions if p.strategy == candidate.strategy)
    checks.append(
        LimitCheck(
            "open trades of the strategy",
            of_strategy < settings.max_open_per_strategy,
            of_strategy,
            settings.max_open_per_strategy,
            candidate.strategy,
            LIMIT_BLOCK,
        ),
    )
    today = picture.entries_today(settings)
    checks.append(
        LimitCheck(
            "trades today",
            today < settings.max_trades_per_day,
            today,
            settings.max_trades_per_day,
            f"trading day {state.day or money.day}",
            LIMIT_BLOCK,
        ),
    )

    open_risk = sum(p.counted_risk for p in counted)
    total = (open_risk + candidate.risk_money) / capital * 100.0 if capital > 0 else math.inf
    checks.append(
        LimitCheck(
            "total open risk",
            total <= settings.max_total_open_risk_percent + 1e-9,
            round(total, 3),
            settings.max_total_open_risk_percent,
            f"open {open_risk:,.2f} + this trade {candidate.risk_money:,.2f} {picture.currency}",
            EXPOSURE_BLOCK,
        ),
    )

    new_item = ExposureItem(
        candidate.base,
        candidate.quote,
        candidate.direction,
        candidate.risk_money,
    )
    after = as_percent(currency_exposure([*_items(counted), new_item]), capital)
    touched = [name for name in (candidate.base, candidate.quote) if name]
    worst = max((abs(after.get(name, 0.0)) for name in touched), default=0.0)
    checks.append(
        LimitCheck(
            "currency exposure",
            worst <= settings.max_currency_exposure_percent + 1e-9,
            round(worst, 3),
            settings.max_currency_exposure_percent,
            f"after this trade: {exposure_text({n: after.get(n, 0.0) for n in touched})}",
            EXPOSURE_BLOCK,
        ),
    )

    if candidate.stops_level > 0:
        checks.append(
            LimitCheck(
                "stop distance",
                candidate.stop_points >= candidate.stops_level,
                round(candidate.stop_points, 1),
                candidate.stops_level,
                "points from the entry to the SL vs the broker's stops level",
                LIMIT_BLOCK,
            ),
        )
    else:
        checks.append(
            LimitCheck(
                "stop distance",
                True,
                round(candidate.stop_points, 1),
                0,
                "the broker has no minimum stop distance",
            ),
        )

    checks.append(_margin_check(candidate, picture, settings))
    return checks


def _margin_check(
    candidate: Candidate,
    picture: AccountPicture,
    settings: RiskSettings,
) -> LimitCheck:
    required = candidate.margin_required
    if required is None or not math.isfinite(required) or required < 0:
        return LimitCheck(
            "margin",
            False,
            None,
            settings.margin_level_floor_percent,
            "MT5 could not calculate the margin (order_calc_margin)",
            MARGIN_BLOCK,
        )
    margin_after = picture.margin + required
    free_after = picture.equity - margin_after
    level = picture.equity / margin_after * 100.0 if margin_after > 0 else math.inf
    passed = free_after > 0 and level >= settings.margin_level_floor_percent
    shown = round(level, 1) if math.isfinite(level) else "no margin used"
    return LimitCheck(
        "margin",
        passed,
        shown,
        settings.margin_level_floor_percent,
        f"needs {required:,.2f} {picture.currency}; margin level after the trade, free margin "
        f"after {free_after:,.2f}",
        MARGIN_BLOCK,
    )


def failed(checks: Sequence[LimitCheck]) -> list[LimitCheck]:
    return [check for check in checks if not check.passed]
