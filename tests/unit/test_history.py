from app.domain import history
from app.domain.history import (
    Deal,
    server_time_to_utc,
    summarize_position,
    summarize_positions,
    trade_source,
)
from app.mt5 import api

OPEN = 1_727_000_000


def deal(ticket: int, position: int, entry: int, deal_type: int, **values: object) -> Deal:
    fields: dict[str, object] = {
        "ticket": ticket,
        "order": ticket,
        "time": OPEN,
        "time_msc": 0,
        "type": deal_type,
        "entry": entry,
        "magic": 0,
        "position_id": position,
        "reason": 0,
        "volume": 0.1,
        "price": 1.08,
        "commission": 0.0,
        "swap": 0.0,
        "profit": 0.0,
        "fee": 0.0,
        "symbol": "EURUSD",
        "comment": "",
    }
    fields.update(values)
    return Deal(**fields)  # type: ignore[arg-type]


BUY, SELL = history.DEAL_TYPE_BUY, history.DEAL_TYPE_SELL
IN, OUT = history.DEAL_ENTRY_IN, history.DEAL_ENTRY_OUT


def test_a_closed_buy_is_rebuilt_with_its_costs() -> None:
    summary = summarize_position(
        [
            deal(2, 7, OUT, SELL, time=OPEN + 3600, price=1.0815, profit=15.0, swap=-0.4, reason=5),
            deal(1, 7, IN, BUY, commission=-0.35),
        ],
    )
    assert summary is not None
    assert (summary.direction, summary.open_time, summary.close_time) == ("buy", OPEN, OPEN + 3600)
    assert (summary.open_price, summary.close_price) == (1.08, 1.0815)
    assert summary.net_profit == 14.25
    assert (summary.outcome, summary.exit_reason) == ("win", "take profit")
    assert summary.duration_sec == 3600


def test_a_partly_closed_position_stays_open() -> None:
    deals = [
        deal(1, 8, IN, SELL, volume=0.3),
        deal(2, 8, OUT, BUY, volume=0.1, time=OPEN + 60, profit=-3.0),
    ]
    summary = summarize_position(deals)
    assert summary is not None and not summary.closed
    assert (summary.volume, summary.closed_volume, summary.outcome) == (0.3, 0.1, "open")
    assert summary.close_time is None and summary.duration_sec is None
    deals.append(deal(3, 8, OUT, BUY, volume=0.2, time=OPEN + 120, price=1.07, profit=-1.0))
    summary = summarize_position(deals)
    assert summary is not None and summary.closed
    assert summary.close_price == round((1.08 * 0.1 + 1.07 * 0.2) / 0.3, 10)
    assert (summary.direction, summary.outcome, summary.net_profit) == ("sell", "loss", -4.0)


def test_money_deals_and_positions_without_an_entry_are_ignored() -> None:
    deals = [
        deal(1, 0, IN, history.DEAL_TYPE_BUY + 2, profit=1000.0),
        deal(2, 9, OUT, SELL, profit=5.0),
        deal(3, 10, IN, BUY, time=OPEN + 5),
        deal(4, 10, OUT, SELL, time=OPEN + 9),
    ]
    found = summarize_positions(deals)
    assert [summary.position_id for summary in found] == [10]
    assert found[0].outcome == "breakeven"


def test_positions_are_listed_oldest_first() -> None:
    deals = [deal(5, 2, IN, BUY, time=OPEN + 50), deal(6, 1, IN, BUY, time=OPEN + 10)]
    assert [summary.position_id for summary in summarize_positions(deals)] == [1, 2]


def test_trade_source_tells_bot_manual_and_other_experts_apart() -> None:
    assert trade_source(0) == "manual"
    assert trade_source(26_000_001, {26_000_001}) == "bot"
    assert trade_source(4242) == "external"


def test_server_time_is_converted_to_utc_with_the_broker_offset() -> None:
    assert server_time_to_utc(OPEN, 3.0) == "2024-09-22T07:13:20.000Z"
    assert server_time_to_utc(OPEN, 0.0) == "2024-09-22T10:13:20.000Z"


def test_the_deal_constants_mirror_the_mt5_api_constants() -> None:
    types = (history.DEAL_TYPE_BUY, history.DEAL_TYPE_SELL)
    assert types == (api.DEAL_TYPE_BUY, api.DEAL_TYPE_SELL)
    entries = (history.DEAL_ENTRY_IN, history.DEAL_ENTRY_OUT, history.DEAL_ENTRY_INOUT)
    assert entries == (api.DEAL_ENTRY_IN, api.DEAL_ENTRY_OUT, api.DEAL_ENTRY_INOUT)
    assert history.DEAL_ENTRY_OUT_BY == api.DEAL_ENTRY_OUT_BY
    reasons = {api.DEAL_REASON_SL: "stop loss", api.DEAL_REASON_TP: "take profit"}
    assert all(history.EXIT_REASONS[key] == value for key, value in reasons.items())
    assert history.EXIT_REASONS[api.DEAL_REASON_SO] == "stop out"
