"""MT5 reads for the risk manager (spec C6). Run in the gateway thread; read-only.

`order_calc_profit` gives the loss of a lot at the stop loss in the account currency, so JPY
quotes, gold and cross-currency accounts are converted by MT5 itself; the symbol's tick value
is never used (ADR 60: on a real FIBO account XAUUSD reported 0.1 USD where a point is worth
1 USD). `order_calc_margin` gives the margin a new trade needs. Positions, the account and
today's deals come back as one `AccountPicture`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, MutableMapping
from typing import Any

from app.domain.history import DEAL_ENTRY_IN
from app.domain.signals import Direction
from app.mt5 import api
from app.mt5.api import MT5Api
from app.mt5.gateway import MT5Gateway
from app.mt5.history_sync import deal_from_mt5
from app.mt5.models import AccountSnapshot
from app.risk.limits import AccountPicture, OpenPosition
from app.risk.limits_state import AccountMoney

DAY_SECONDS = 86_400
REQUEST_TIMEOUT_SECONDS = 30.0
Currencies = MutableMapping[str, tuple[str, str]]


def order_action(direction: Direction) -> int:
    """`order_calc_*` take ORDER_TYPE_BUY or ORDER_TYPE_SELL, also for stop and limit orders."""
    return api.ORDER_TYPE_BUY if direction is Direction.LONG else api.ORDER_TYPE_SELL


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def loss_per_lot(
    mt5: MT5Api,
    symbol: str,
    direction: Direction,
    entry: float,
    sl: float,
    volume: float = 1.0,
) -> float | None:
    """The money `volume` lots lose from `entry` to `sl` (positive), or None if MT5 failed."""
    profit = _finite(mt5.order_calc_profit(order_action(direction), symbol, volume, entry, sl))
    return -profit / volume if profit is not None and volume > 0 else None


def margin_for(
    mt5: MT5Api,
    symbol: str,
    direction: Direction,
    volume: float,
    price: float,
) -> float | None:
    return _finite(mt5.order_calc_margin(order_action(direction), symbol, volume, price))


def _currencies(mt5: MT5Api, symbol: str, cache: Currencies) -> tuple[str, str]:
    found = cache.get(symbol)
    if found is None:
        info = mt5.symbol_info(symbol)
        base = str(getattr(info, "currency_margin", "") or "") if info is not None else ""
        quote = str(getattr(info, "currency_profit", "") or "") if info is not None else ""
        found = (base or symbol[:3].upper(), quote or symbol[3:6].upper())
        cache[symbol] = found
    return found


def read_position(
    mt5: MT5Api,
    raw: Any,
    cache: Currencies,
    strategy_of: Callable[[int], str],
) -> OpenPosition:
    symbol = str(getattr(raw, "symbol", "") or "")
    long = int(getattr(raw, "type", 0) or 0) == api.POSITION_TYPE_BUY
    direction = Direction.LONG if long else Direction.SHORT
    volume = float(getattr(raw, "volume", 0.0) or 0.0)
    price_open = float(getattr(raw, "price_open", 0.0) or 0.0)
    sl = float(getattr(raw, "sl", 0.0) or 0.0)
    magic = int(getattr(raw, "magic", 0) or 0)
    risk: float | None = None
    if sl > 0 and volume > 0:
        loss = loss_per_lot(mt5, symbol, direction, price_open, sl, volume)
        risk = max(0.0, loss * volume) if loss is not None else None
    base, quote = _currencies(mt5, symbol, cache)
    return OpenPosition(
        ticket=int(getattr(raw, "ticket", 0) or 0),
        symbol=symbol,
        direction=direction,
        volume=volume,
        price_open=price_open,
        sl=sl,
        profit=float(getattr(raw, "profit", 0.0) or 0.0),
        magic=magic,
        risk_money=risk,
        base=base,
        quote=quote,
        strategy=strategy_of(magic),
    )


def read_picture(
    mt5: MT5Api,
    *,
    day: str,
    day_start_server: int,
    read_at: float,
    strategy_of: Callable[[int], str],
    cache: Currencies,
) -> AccountPicture | None:
    """Account, positions and today's deals. `day_start_server` is the broker midnight in
    server time (MT5 deal times are server time). None when MT5 gave no account."""
    info = mt5.account_info()
    if info is None:
        return None
    account = AccountSnapshot.from_mt5(info)
    positions = tuple(
        read_position(mt5, raw, cache, strategy_of) for raw in (mt5.positions_get() or ())
    )
    until = int(read_at) + 2 * DAY_SECONDS  # server time runs ahead of UTC
    raw_deals = mt5.history_deals_get(day_start_server - DAY_SECONDS, until)
    deals = [deal_from_mt5(raw) for raw in (raw_deals or ())]
    today = [deal for deal in deals if deal.time >= day_start_server]
    realized = sum(d.profit + d.commission + d.swap + d.fee for d in today if d.is_trade)
    deposits = sum(d.profit for d in today if d.type == api.DEAL_TYPE_BALANCE)
    entries = [d for d in today if d.is_trade and d.entry == DEAL_ENTRY_IN]
    money = AccountMoney(day, account.balance, account.equity, realized, deposits)
    return AccountPicture(
        currency=account.currency,
        balance=account.balance,
        equity=account.equity,
        margin=account.margin,
        margin_free=account.margin_free,
        margin_level=account.margin_level,
        positions=positions,
        money=money,
        bot_entries_today=sum(1 for d in entries if strategy_of(d.magic)),
        manual_entries_today=sum(1 for d in entries if not strategy_of(d.magic)),
        read_at=read_at,
    )


class GatewayRiskBroker:
    """`RiskBroker` through the MT5 gateway thread. Called from the analysis thread."""

    def __init__(
        self,
        gateway: MT5Gateway,
        strategy_of: Callable[[int], str],
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._gateway = gateway
        self._strategy_of = strategy_of
        self._timeout = timeout
        self._currencies: Currencies = {}

    def picture(
        self,
        *,
        day: str,
        day_start_server: int,
        read_at: float,
    ) -> AccountPicture | None:
        def work(mt5: MT5Api) -> AccountPicture | None:
            return read_picture(
                mt5,
                day=day,
                day_start_server=day_start_server,
                read_at=read_at,
                strategy_of=self._strategy_of,
                cache=self._currencies,
            )

        return self._gateway.run("risk_account_read", work, timeout=self._timeout)

    def loss_per_lot(
        self,
        symbol: str,
        direction: Direction,
        entry: float,
        sl: float,
    ) -> float | None:
        arguments = {"symbol": symbol, "direction": direction.value, "entry": entry, "sl": sl}
        return self._gateway.run(
            "order_calc_profit",
            lambda mt5: loss_per_lot(mt5, symbol, direction, entry, sl),
            timeout=self._timeout,
            arguments=arguments,
        )

    def margin(
        self,
        symbol: str,
        direction: Direction,
        volume: float,
        price: float,
    ) -> float | None:
        arguments = {"symbol": symbol, "direction": direction.value, "volume": volume}
        return self._gateway.run(
            "order_calc_margin",
            lambda mt5: margin_for(mt5, symbol, direction, volume, price),
            timeout=self._timeout,
            arguments=arguments,
        )
