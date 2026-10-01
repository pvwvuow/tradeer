"""Import the MT5 deal and order history into the local database (spec C11, E2).

Runs after every connection and when the user presses "Import now". The first import reads
everything since 2000; later imports re-read the last 3 days (brokers can book deals late).
Deals are saved by ticket and trades are rebuilt from all deals of each touched position, with
ids derived from the account and position, so importing twice never creates a duplicate.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

from app.domain.history import Deal, Order, summarize_positions
from app.mt5 import api
from app.mt5.api import MT5Api
from app.mt5.errors import error_from_last
from app.mt5.gateway import MT5Gateway
from app.mt5.models import AccountSnapshot
from app.storage.repositories import Store

HISTORY_START = 946_684_800  # 2000-01-01 00:00 UTC
OVERLAP_SECONDS = 3 * 86_400
AHEAD_SECONDS = 2 * 86_400  # broker server time can be ahead of UTC
IMPORT_TIMEOUT_SECONDS = 180.0


@dataclass(frozen=True)
class HistoryResult:
    deals: int
    new_deals: int
    orders: int
    trades: int
    changed_trades: int
    offset_hours: float | None

    def text(self) -> str:
        offset = (
            f"broker time UTC{self.offset_hours:+g}"
            if self.offset_hours is not None
            else "broker time offset unknown (market closed), times saved as server time"
        )
        return (
            f"Read {self.deals:,} deals ({self.new_deals:,} new) and {self.orders:,} orders; "
            f"{self.trades:,} trades rebuilt, {self.changed_trades:,} new or changed; {offset}."
        )


def _number(source: Any, name: str) -> Any:
    value = getattr(source, name, 0)
    return value if value is not None else 0


def deal_from_mt5(raw: Any) -> Deal:
    return Deal(
        ticket=int(_number(raw, "ticket")),
        order=int(_number(raw, "order")),
        time=int(_number(raw, "time")),
        time_msc=int(_number(raw, "time_msc")),
        type=int(_number(raw, "type")),
        entry=int(_number(raw, "entry")),
        magic=int(_number(raw, "magic")),
        position_id=int(_number(raw, "position_id")),
        reason=int(_number(raw, "reason")),
        volume=float(_number(raw, "volume")),
        price=float(_number(raw, "price")),
        commission=float(_number(raw, "commission")),
        swap=float(_number(raw, "swap")),
        profit=float(_number(raw, "profit")),
        fee=float(_number(raw, "fee")),
        symbol=str(getattr(raw, "symbol", "") or ""),
        comment=str(getattr(raw, "comment", "") or ""),
    )


def order_from_mt5(raw: Any) -> Order:
    return Order(
        ticket=int(_number(raw, "ticket")),
        time_setup=int(_number(raw, "time_setup")),
        time_done=int(_number(raw, "time_done")),
        type=int(_number(raw, "type")),
        state=int(_number(raw, "state")),
        magic=int(_number(raw, "magic")),
        position_id=int(_number(raw, "position_id")),
        volume_initial=float(_number(raw, "volume_initial")),
        volume_current=float(_number(raw, "volume_current")),
        price_open=float(_number(raw, "price_open")),
        sl=float(_number(raw, "sl")),
        tp=float(_number(raw, "tp")),
        price_current=float(_number(raw, "price_current")),
        symbol=str(getattr(raw, "symbol", "") or ""),
        comment=str(getattr(raw, "comment", "") or ""),
    )


def _records(mt5: MT5Api, result: Any) -> tuple[Any, ...]:
    """`None` from MT5 means an error, unless `last_error()` still says success."""
    if result is None:
        code, detail = mt5.last_error()
        if code != api.RES_S_OK:
            raise error_from_last((code, detail))
        return ()
    return tuple(result)


def read_history(mt5: MT5Api, date_from: int, date_to: int) -> tuple[list[Deal], list[Order]]:
    """Runs in the gateway thread. Read-only: never an order function."""
    deals = _records(mt5, mt5.history_deals_get(date_from, date_to))
    orders = _records(mt5, mt5.history_orders_get(date_from, date_to))
    return [deal_from_mt5(deal) for deal in deals], [order_from_mt5(order) for order in orders]


class HistoryImporter:
    def __init__(
        self,
        gateway: MT5Gateway,
        store: Store,
        *,
        bot_magics: Collection[int] = (),
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._gateway = gateway
        self._store = store
        self._bot_magics = bot_magics
        self._clock = clock
        self._lock = threading.Lock()

    def run(self, account: AccountSnapshot, offset_hours: float | None) -> HistoryResult:
        """Import new history for `account`. Blocks: call it from a worker thread."""
        with self._lock:
            key = self._store.upsert_account(account)
            state_key = f"history_until:{key}"
            saved = self._store.get_state(state_key)
            since = max(HISTORY_START, int(saved) - OVERLAP_SECONDS) if saved else HISTORY_START
            until = int(self._clock()) + AHEAD_SECONDS
            deals, orders = self._gateway.run(
                "history_import",
                lambda mt5: read_history(mt5, since, until),
                timeout=IMPORT_TIMEOUT_SECONDS,
                arguments={"date_from": since, "date_to": until},
            )
            new_deals = self._store.upsert_deals(key, deals)
            self._store.upsert_orders(key, orders)
            touched = {deal.position_id for deal in deals if deal.is_trade}
            summaries = summarize_positions(self._store.deals_for_positions(key, touched))
            changed = self._store.upsert_trades(
                key,
                summaries,
                offset_hours=offset_hours or 0.0,
                bot_magics=self._bot_magics,
            )
            newest = max((deal.time for deal in deals), default=None)
            if newest is not None:
                self._store.set_state(state_key, str(max(newest, int(saved or 0))))
            return HistoryResult(
                deals=len(deals),
                new_deals=new_deals,
                orders=len(orders),
                trades=len(summaries),
                changed_trades=changed,
                offset_hours=offset_hours,
            )
