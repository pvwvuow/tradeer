"""The paper broker (spec C8) on scripted quotes: fills, pending orders, SL/TP, commission,
the saved state and the paper risk picture."""

from dataclasses import dataclass, field

from app.brokers.base import BrokerPosition
from app.brokers.market import Quote
from app.brokers.paper_broker import FIRST_TICKET, ModeRiskBroker, PaperBroker, risk_account
from app.brokers.requests import OrderPlan
from app.core.execution_settings import ExecutionSettings
from app.domain.history import summarize_position
from app.domain.signals import Direction, OrderType
from app.mt5.models import SymbolSpec, SymbolTradeMode
from app.risk.limits import AccountPicture
from app.risk.limits_state import AccountMoney

MAGIC = 26_070_001


def spec(name: str = "EURUSD") -> SymbolSpec:
    return SymbolSpec(
        name=name,
        description="",
        digits=5,
        point=1e-5,
        tick_size=1e-5,
        tick_value=1.0,
        contract_size=100_000.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=10,
        freeze_level=0,
        filling_mode=3,
        trade_mode=SymbolTradeMode.FULL,
        currency_margin="EUR",
        currency_profit="USD",
        visible=True,
    )


@dataclass
class Market:
    bid: float = 1.10000
    spread: float = 0.00010
    time: int = 1_790_000_000
    balance_value: float | None = 5_000.0

    def quote(self, symbol: str) -> Quote | None:
        return Quote(self.bid, round(self.bid + self.spread, 5), self.time)

    def spec(self, symbol: str) -> SymbolSpec | None:
        return spec(symbol)

    def balance(self) -> float | None:
        return self.balance_value

    def profit(
        self,
        symbol: str,
        direction: Direction,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> float | None:
        return (price_close - price_open) * direction.sign * 100_000.0 * volume


@dataclass
class KeyValue:
    values: dict[str, str] = field(default_factory=dict)

    def get_state(self, key: str) -> str | None:
        return self.values.get(key)

    def set_state(self, key: str, value: str) -> None:
        self.values[key] = value


def broker(market: Market, store: KeyValue, **settings: object) -> PaperBroker:
    found = ExecutionSettings(**settings)  # type: ignore[arg-type]
    return PaperBroker(market, lambda: found, store=store, account=lambda: "acc")


def buy(**changes: object) -> OrderPlan:
    values: dict[str, object] = {
        "symbol": "EURUSD",
        "direction": Direction.LONG,
        "order_type": OrderType.MARKET,
        "volume": 0.5,
        "price": 1.1001,
        "sl": 1.09900,
        "tp": 1.10200,
        "magic": MAGIC,
        "comment": "tw-abc",
        "deviation": 10,
        "filling": 0,
    }
    values.update(changes)
    return OrderPlan(**values)  # type: ignore[arg-type]


def only(paper: PaperBroker) -> BrokerPosition:
    positions = paper.positions([MAGIC]) or []
    assert len(positions) == 1
    return positions[0]


def test_a_market_fill_at_the_ask_plus_slippage_hits_the_take_profit() -> None:
    market, store = Market(), KeyValue()
    paper = broker(market, store, paper_slippage_points=2, paper_commission_per_lot=7.0)
    assert paper.balance == 5_000.0  # the MT5 balance when paper trading starts
    result = paper.open(buy())
    assert result.ok and result.position >= FIRST_TICKET
    assert abs(result.price - 1.10012) < 1e-9 and result.requested_price == 1.1001
    market.bid = 1.10100
    assert paper.step() == []
    assert round(only(paper).profit, 2) == 44.0
    market.bid = 1.10210
    [event] = paper.step()
    assert "take profit" in event and paper.positions([MAGIC]) == []
    summary = summarize_position(list(paper.deals(result.position) or []))
    assert summary is not None and summary.closed and summary.exit_reason == "take profit"
    assert round(summary.profit, 2) == 94.0 and round(summary.commission, 2) == -3.5
    assert round(paper.balance, 2) == 5_090.5


def test_the_stop_loss_fills_at_the_worse_price_on_a_gap() -> None:
    market, store = Market(), KeyValue()
    paper = broker(market, store, paper_slippage_points=0)
    result = paper.open(buy())
    market.bid = 1.09850  # gapped through the stop
    paper.step()
    summary = summarize_position(list(paper.deals(result.position) or []))
    assert summary is not None and summary.exit_reason == "stop loss"
    assert summary.close_price == 1.09850 and round(summary.profit, 2) == -80.0


def test_pending_orders_fill_when_crossed_and_expire() -> None:
    market, store = Market(), KeyValue()
    paper = broker(market, store, paper_slippage_points=0)
    stop = paper.open(buy(order_type=OrderType.STOP, price=1.10050, sl=1.09950, tp=1.10250))
    assert stop.ok and stop.placed and paper.positions([MAGIC]) == []
    market.bid = 1.10045  # the ask reaches 1.10055
    [event] = paper.step()
    assert "filled" in event and only(paper).ticket == stop.order
    late = paper.open(
        buy(order_type=OrderType.STOP, price=1.10500, sl=1.10400, tp=1.10700, expiration=1),
    )
    assert late.ok
    assert any("expired" in line for line in paper.step())
    assert paper.orders([MAGIC]) == []
    refused = paper.open(buy(order_type=OrderType.STOP, price=1.10000))
    assert not refused.ok and "past the pending entry" in refused.text


def test_invalid_stops_are_refused_like_mt5() -> None:
    paper = broker(Market(), KeyValue())
    result = paper.open(buy(sl=1.10005))
    assert not result.ok and "invalid stops" in result.text


def test_modify_partial_close_and_the_state_survive_a_restart() -> None:
    market, store = Market(), KeyValue()
    paper = broker(market, store, paper_slippage_points=0)
    result = paper.open(buy())
    position = only(paper)
    assert paper.modify(position, 1.10000, position.tp).ok
    assert paper.close(position, 0.2).ok
    again = broker(market, store, paper_slippage_points=0)
    restored = only(again)
    assert restored.ticket == result.position and restored.sl == 1.10000
    assert restored.volume == 0.3
    gone = BrokerPosition(1, "EURUSD", Direction.LONG, 1, 1, 0, 0, 0, 0, 0, "", 0)
    assert not again.close(gone).ok


@dataclass
class Live:
    found: AccountPicture

    def picture(self, *, day: str, day_start_server: int, read_at: float) -> AccountPicture:
        return self.found

    def loss_per_lot(self, symbol: str, direction: Direction, entry: float, sl: float) -> float:
        return 100.0

    def margin(self, symbol: str, direction: Direction, volume: float, price: float) -> float:
        return 50.0


def test_paper_risk_uses_paper_money_and_positions() -> None:
    market, store = Market(), KeyValue()
    paper = broker(market, store, paper_slippage_points=0)
    paper.open(buy())
    money = AccountMoney("2026-09-30", 20_000.0, 20_000.0, 0.0, 0.0)
    live = AccountPicture("USD", 20_000.0, 20_000.0, 0, 20_000.0, 0, (), money, 0, 2, 0.0)

    mode = [True]
    risk = ModeRiskBroker(Live(live), paper, lambda: mode[0], lambda magic: "trend_pullback")
    found = risk.picture(day="2026-09-30", day_start_server=0, read_at=0.0)
    assert found is not None and found.balance == 5_000.0
    [position] = found.positions
    assert position.strategy == "trend_pullback" and round(position.risk_money or 0, 2) == 55.0
    assert found.bot_entries_today == 1 and found.manual_entries_today == 0
    mode[0] = False
    assert risk.picture(day="2026-09-30", day_start_server=0, read_at=0.0) is live
    assert risk_account("acc", True) == "paper:acc" and risk_account("acc", False) == "acc"
