"""The replay's market (spec C8): the quote at the replay time, the symbol specification, and
the account reads the risk manager makes, all from the backtest's bars and cost model.

`ReplayMarket` is the `MarketReads` of the backtest broker and the execution engine, and the
`RiskBroker` of the risk manager, so neither knows it runs on history.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from app.backtest import costs as cost_model
from app.backtest.costs import BacktestCosts
from app.brokers.backtest_broker import BacktestBroker
from app.brokers.market import Quote
from app.domain.signals import Direction
from app.mt5.models import SymbolSpec
from app.risk.limits import AccountPicture
from app.risk.limits_state import AccountMoney


class ReplayMarket:
    def __init__(
        self,
        specs: Mapping[str, SymbolSpec],
        costs: BacktestCosts,
        strategy_of: Callable[[int], str],
    ) -> None:
        self._specs = dict(specs)
        self._costs = costs
        self._strategy_of = strategy_of
        self._quotes: dict[str, Quote] = {}
        self.broker: BacktestBroker | None = None

    # The replay sets the price --------------------------------------------------------------
    def set_quote(self, symbol: str, bid: float, spread: float, server_time: int) -> None:
        self._quotes[symbol] = Quote(bid, bid + spread, int(server_time))

    # MarketReads --------------------------------------------------------------------------
    def quote(self, symbol: str) -> Quote | None:
        return self._quotes.get(symbol)

    def spec(self, symbol: str) -> SymbolSpec | None:
        return self._specs.get(symbol)

    def balance(self) -> float | None:
        return self._costs.start_balance

    def profit(
        self,
        symbol: str,
        direction: Direction,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> float | None:
        spec = self._specs.get(symbol)
        if spec is None:
            return None
        return cost_model.profit(spec, self._costs, direction, volume, price_open, price_close)

    # RiskBroker ---------------------------------------------------------------------------
    def picture(
        self,
        *,
        day: str,
        day_start_server: int,
        read_at: float,
    ) -> AccountPicture | None:
        broker = self.broker
        if broker is None:
            return None
        balance = broker.balance
        base = AccountPicture(
            currency=self._costs.account_currency.upper(),
            balance=balance,
            equity=balance,
            margin=0.0,
            margin_free=balance,
            margin_level=0.0,
            positions=(),
            money=AccountMoney(day, balance, balance, 0.0, 0.0),
            bot_entries_today=0,
            manual_entries_today=0,
            read_at=read_at,
        )
        return broker.picture(
            base,
            day=day,
            day_start_server=day_start_server,
            strategy_of=self._strategy_of,
        )

    def loss_per_lot(
        self,
        symbol: str,
        direction: Direction,
        entry: float,
        sl: float,
    ) -> float | None:
        found = self.profit(symbol, direction, 1.0, entry, sl)
        return -found if found is not None else None  # positive: the loss (as live)

    def margin(
        self,
        symbol: str,
        direction: Direction,
        volume: float,
        price: float,
    ) -> float | None:
        spec = self._specs.get(symbol)
        if spec is None:
            return None
        return cost_model.margin(spec, self._costs, volume, price)
