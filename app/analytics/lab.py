"""Strategy lab review: every strategy's numbers side by side, with its strong and weak
spots in plain words, so a demo account that runs all strategies at once shows which one
earns its place (asked for on 7 October 2026). Pure: closed trades in, review out.

A spot needs `MIN_GROUP` trades before it is named, and a verdict needs `VERDICT_TRADES`
(the Go-Live gate's 30): fewer trades are reported as too few to judge.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.analytics.trades import TradeRecord

MIN_GROUP = 3
VERDICT_TRADES = 30
STOP_EXIT = "stop loss"
MANY_STOPS = 0.6  # share of trades ending at the stop loss
GAVE_BACK_R = 1.0  # a loser that was this far in profit first
MANY_GAVE_BACK = 0.3  # share of losers
DEEP_MAE_R = -0.7  # winners that went this far against first, on average
GOOD_PF = 1.3
NOT_STRATEGIES = ("manual", "unknown")


@dataclass(frozen=True)
class StrategyReview:
    strategy: str
    trades: int
    wins: int
    net_profit: float
    profit_factor: float | None  # None without a losing trade
    expectancy_r: float | None  # None without an R on any trade
    strengths: tuple[str, ...] = field(default_factory=tuple)
    weaknesses: tuple[str, ...] = field(default_factory=tuple)

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0

    @property
    def judged(self) -> bool:
        return self.trades >= VERDICT_TRADES

    def line(self, currency: str = "") -> str:
        unit = f" {currency}" if currency else ""
        parts = [
            f"{self.trades} trade(s)",
            f"win rate {self.win_rate * 100:.0f}%",
            f"net {self.net_profit:+,.2f}{unit}",
        ]
        if self.profit_factor is not None:
            parts.append(f"profit factor {self.profit_factor:.2f}")
        if self.expectancy_r is not None:
            parts.append(f"expectancy {self.expectancy_r:+.2f} R")
        if not self.judged:
            parts.append(f"too few to judge ({self.trades}/{VERDICT_TRADES})")
        return f"{self.strategy}: " + ", ".join(parts)

    def as_dict(self) -> dict[str, object]:
        return {
            "strategy": self.strategy,
            "trades": self.trades,
            "wins": self.wins,
            "win_rate": round(self.win_rate, 4),
            "net_profit": round(self.net_profit, 2),
            "profit_factor": self.profit_factor,
            "expectancy_r": self.expectancy_r,
            "strengths": list(self.strengths),
            "weaknesses": list(self.weaknesses),
        }


def _profit_factor(trades: Sequence[TradeRecord]) -> float | None:
    won = sum(t.net_profit for t in trades if t.net_profit > 0)
    lost = -sum(t.net_profit for t in trades if t.net_profit < 0)
    return round(won / lost, 2) if lost > 0 else None


def _expectancy_r(trades: Sequence[TradeRecord]) -> float | None:
    found = [t.r_multiple for t in trades if t.r_multiple is not None]
    return round(sum(found) / len(found), 3) if found else None


def _groups(
    trades: Sequence[TradeRecord],
    key: Callable[[TradeRecord], str],
) -> list[tuple[str, list[TradeRecord]]]:
    found: dict[str, list[TradeRecord]] = {}
    for trade in trades:
        name = key(trade)
        if name:
            found.setdefault(name, []).append(trade)
    return [(name, group) for name, group in found.items() if len(group) >= MIN_GROUP]


def _spot(name: str, group: Sequence[TradeRecord]) -> str:
    wins = sum(1 for t in group if t.win)
    net = sum(t.net_profit for t in group)
    return f"{name} ({len(group)} trades, {wins * 100 / len(group):.0f}% won, net {net:+,.2f})"


def _spots(trades: Sequence[TradeRecord]) -> tuple[list[str], list[str]]:
    """The best and the worst symbol, session and side, when they won or lost money."""
    strengths: list[str] = []
    weaknesses: list[str] = []
    keys: tuple[tuple[str, Callable[[TradeRecord], str]], ...] = (
        ("symbol", lambda t: t.symbol),
        ("session", lambda t: t.session),
        ("side", lambda t: t.direction),
    )
    for label, key in keys:
        groups = _groups(trades, key)
        if len(groups) < 2:
            continue
        ranked = sorted(groups, key=lambda item: sum(t.net_profit for t in item[1]))
        worst_name, worst = ranked[0]
        best_name, best = ranked[-1]
        if sum(t.net_profit for t in best) > 0:
            strengths.append(f"best {label}: {_spot(best_name, best)}")
        if sum(t.net_profit for t in worst) < 0:
            weaknesses.append(f"worst {label}: {_spot(worst_name, worst)}")
    return strengths, weaknesses


def _habits(trades: Sequence[TradeRecord]) -> tuple[list[str], list[str]]:
    """How the trades end: stops, given-back profits, deep pullbacks before a win."""
    strengths: list[str] = []
    weaknesses: list[str] = []
    if len(trades) >= MIN_GROUP:
        stops = sum(1 for t in trades if t.exit_reason == STOP_EXIT) / len(trades)
        if stops >= MANY_STOPS:
            weaknesses.append(f"{stops * 100:.0f}% of the trades end at the stop loss")
    losers = [t for t in trades if t.loss]
    gave_back = [t for t in losers if t.mfe_r is not None and t.mfe_r >= GAVE_BACK_R]
    if len(losers) >= MIN_GROUP and len(gave_back) / len(losers) >= MANY_GAVE_BACK:
        weaknesses.append(
            f"{len(gave_back)} of {len(losers)} losers were {GAVE_BACK_R:g} R or more in "
            "profit first: move the stop to break-even sooner or take part of the profit",
        )
    winners = [t for t in trades if t.win and t.mae_r is not None]
    if len(winners) >= MIN_GROUP:
        mae = sum(t.mae_r or 0.0 for t in winners) / len(winners)
        if mae <= DEEP_MAE_R:
            weaknesses.append(
                f"winners first went {mae:.2f} R against the trade on average: the entries "
                "are early",
            )
        elif mae > DEEP_MAE_R / 2:
            strengths.append(f"clean entries: winners went only {mae:.2f} R against first")
    profit_factor = _profit_factor(trades)
    if profit_factor is not None and profit_factor >= GOOD_PF and len(trades) >= MIN_GROUP:
        strengths.append(f"profit factor {profit_factor:.2f}")
    return strengths, weaknesses


def review_strategies(trades: Sequence[TradeRecord]) -> list[StrategyReview]:
    """One review per strategy with closed trades, best expectancy (then net) first."""
    by_strategy: dict[str, list[TradeRecord]] = {}
    for trade in trades:
        if trade.strategy and trade.strategy not in NOT_STRATEGIES:
            by_strategy.setdefault(trade.strategy, []).append(trade)
    reviews: list[StrategyReview] = []
    for name, group in by_strategy.items():
        good_spots, bad_spots = _spots(group)
        good_habits, bad_habits = _habits(group)
        reviews.append(
            StrategyReview(
                strategy=name,
                trades=len(group),
                wins=sum(1 for t in group if t.win),
                net_profit=round(sum(t.net_profit for t in group), 2),
                profit_factor=_profit_factor(group),
                expectancy_r=_expectancy_r(group),
                strengths=tuple(good_habits + good_spots),
                weaknesses=tuple(bad_habits + bad_spots),
            ),
        )

    def rank(item: StrategyReview) -> tuple[float, float]:
        expectancy = item.expectancy_r if item.expectancy_r is not None else float("-inf")
        return (expectancy, item.net_profit)

    return sorted(reviews, key=rank, reverse=True)


def review_lines(reviews: Sequence[StrategyReview], currency: str = "") -> list[str]:
    """The review as Markdown list lines for the daily and weekly reports."""
    lines: list[str] = []
    for item in reviews:
        lines.append(f"  - {item.line(currency)}")
        lines.extend(f"    - + {text}" for text in item.strengths)
        lines.extend(f"    - - {text}" for text in item.weaknesses)
    return lines
