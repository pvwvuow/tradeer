"""Signals a person brings (docs/SIGNAL_DESK.md 2.6): pasted in the AI Lab or from a channel.

No rules of its own, so `evaluate` never fires on a closed bar. The Signal desk turns a
checked `OrderPlan` into one signal per target (a leg) and the pipeline runs the same
features, probability, filters, risk limits and approval on each leg as for every strategy.

Every leg carries three features: `confirm` = "user" (Auto never sends it by itself, only
your hold-to-confirm does), `leg_group` (the legs of one signal do not block each other in
the duplicate checks) and `risk_share` (each leg is sized with its share of the risk, so
all legs together risk what one trade may).

A channel's signals (docs/SIGNAL_DESK.md 3.4, phase 21d) are manual signals too, booked to the
channel's own strategy name `channel:<magic>` (26_071_001, 26_071_002, ...), so the history,
the reports and the risk limits keep every channel apart from the others and from pasted
signals. The magic is in the name, never the Telegram chat id.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict

from app.domain.signals import Signal, signal_id
from app.signals.plan import OrderPlan
from app.strategies.base import Condition, Evaluation, SetupState, Strategy
from app.strategies.context import MarketContext

NAME = "manual_signal"
CONFIRM_FEATURE = "confirm"
CONFIRM_USER = "user"
GROUP_FEATURE = "leg_group"
SHARE_FEATURE = "risk_share"
DESK_NOTE = "signals come from the Signal desk, never from a closed bar"
CHANNEL_PREFIX = "channel:"
CHANNEL_MAGICS = range(26_071_001, 26_072_000)  # one per channel, never reused


class ManualSignalParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


def channel_strategy(magic: int) -> str:
    """The strategy name of a channel's signals: `channel:26071001`."""
    if magic not in CHANNEL_MAGICS:
        raise ValueError(f"{magic} is not a channel magic number")
    return f"{CHANNEL_PREFIX}{magic}"


def channel_magic(strategy: str) -> int:
    """The magic number in a channel's strategy name, 0 for every other name."""
    if not strategy.startswith(CHANNEL_PREFIX):
        return 0
    digits = strategy[len(CHANNEL_PREFIX) :]
    if not digits.isdigit():
        return 0
    magic = int(digits)
    return magic if magic in CHANNEL_MAGICS else 0


def with_channels(magics: Mapping[str, int]) -> dict[str, int]:
    """The magic numbers plus every channel's when manual signals have one: the execution
    engine sends and tracks a channel's orders like any other bot order."""
    found = dict(magics)
    if NAME in found:
        found.update({channel_strategy(magic): magic for magic in CHANNEL_MAGICS})
    return found


def needs_confirmation(signal: Signal) -> bool:
    """Only the user's own confirmation may send this signal (never Auto)."""
    return signal.features.get(CONFIRM_FEATURE) == CONFIRM_USER


class ManualSignal(Strategy):
    name = NAME
    version = "1.0.0"
    title = "Manual signal"
    description = "A signal you pasted or a channel sent: you confirm every order."
    params_model = ManualSignalParams
    entry_timeframe = "M15"
    required_history = {"M15": 15}
    example = False

    def evaluate(self, ctx: MarketContext) -> Evaluation:
        return self.result(ctx, SetupState.NONE, (), note=DESK_NOTE)

    def checked(
        self,
        ctx: MarketContext,
        plan: OrderPlan,
        signals: Sequence[Signal],
    ) -> Evaluation:
        """The plan's price checks as the strategy's rules in the decision trace."""
        conditions = tuple(
            Condition(
                check.name,
                check.passed or check.warning,
                detail=check.detail if check.passed else f"warning: {check.detail}",
            )
            for check in plan.checks
        )
        return self.result(ctx, SetupState.READY, conditions, signals)

    def legs(
        self,
        ctx: MarketContext,
        plan: OrderPlan,
        *,
        group: str,
        shares: Sequence[float],
        source: str,
        reason: str,
        created_at: float,
        expires_at: float,
        strategy: str = NAME,
        extra: Mapping[str, float | str] | None = None,
    ) -> tuple[Signal, ...]:
        """One signal per target, the first `len(shares)` targets of the plan, booked to
        `strategy` (this one, or a channel's `channel:<magic>`); `extra` are more features
        (a channel's budget)."""
        assert plan.direction is not None and plan.order is not None
        count = len(shares)
        found: list[Signal] = []
        for index, share in enumerate(shares):
            features: dict[str, float | str] = {
                **(extra or {}),  # first, so nothing in it can change the confirm
                CONFIRM_FEATURE: CONFIRM_USER,
                GROUP_FEATURE: group,
                SHARE_FEATURE: round(share, 6),
                "leg": f"{index + 1}/{count}",
                "source": source,
            }
            found.append(
                Signal(
                    id=signal_id(NAME, f"{group}:{index + 1}", ctx.symbol, "M15", ctx.bar_time),
                    symbol=ctx.symbol,
                    timeframe=self.entry_timeframe,
                    direction=plan.direction,
                    order_type=plan.order,
                    entry=plan.entry,
                    sl=plan.sl,
                    tp=plan.tps[index],
                    reason=reason,
                    strategy=strategy,
                    strategy_version=self.version,
                    params_hash=self.params_hash,
                    bar_time=ctx.bar_time,
                    created_at=created_at,
                    expires_at=expires_at,
                    digits=ctx.digits,
                    features=features,
                ),
            )
        return tuple(found)
