"""Signals from text (docs/SIGNAL_DESK.md 2.2 to 2.4): parser, prefilter, plan and legs."""

import math

from app.domain.signals import Direction, OrderType
from app.signals.legs import split
from app.signals.parse import parse
from app.signals.plan import Market, plan
from app.signals.prefilter import Kind, classify, looks_like_signal
from app.signals.words import normalise, resolve_symbol

BROKER = ("EURUSD.r", "GBPUSD.r", "XAUUSD.r")
GOLD = Market(bid=2343.0, ask=2343.3, atr=4.0, digits=2, point=0.01)


def test_the_four_examples_of_the_spec_parse() -> None:
    zone = parse("XAUUSD BUY 2345-2340  SL 2335  TP1 2350  TP2 2360  TP3 open")
    assert (zone.symbol, zone.direction, zone.order) == ("XAUUSD", Direction.LONG, None)
    assert zone.entry == (2340.0, 2345.0)
    assert zone.sl == 2335.0
    assert zone.tps == (2350.0, 2360.0, None)
    now = parse("GOLD sell now @ 2361 sl 2368 tp 2355 / 2348")
    assert (now.symbol, now.direction, now.order) == ("XAUUSD", Direction.SHORT, OrderType.MARKET)
    assert (now.entry, now.sl, now.tps) == ((2361.0,), 2368.0, (2355.0, 2348.0))
    limit = parse("EURUSD buy limit 1.1150 stop 1.1120 target 1.1210")
    assert (limit.order, limit.entry, limit.sl, limit.tps) == (
        OrderType.LIMIT,
        (1.115,),
        1.112,
        (1.121,),
    )
    persian = parse("طلا فروش ۲۳۶۱ استاپ ۲۳۶۸ تارگت ۲۳۵۵ و ۲۳۴۸")
    assert (persian.symbol, persian.direction) == ("XAUUSD", Direction.SHORT)
    assert (persian.entry, persian.sl, persian.tps) == ((2361.0,), 2368.0, (2355.0, 2348.0))
    assert all(signal.complete for signal in (zone, now, limit, persian))


def test_noise_around_the_numbers_is_left_out() -> None:
    text = "🔥VIP 2🔥 #XAU/USD SELL @ 2,361.50 SL: 2368 TP 1: 2355 TP 2: 2348 (+130 pips) 10:30"
    signal = parse(text)
    assert signal.symbol == "XAUUSD"
    assert signal.entry == (2361.5,)
    assert signal.tps == (2355.0, 2348.0)
    assert signal.ignored == (2.0,)


def test_persian_stop_orders_and_greetings() -> None:
    signal = parse("سلام دوستان، بای استاپ طلا ۲۳۷۰ حد ضرر ۲۳۶۲ حد سود ۲۳۸۰")
    assert (signal.direction, signal.order) == (Direction.LONG, OrderType.STOP)
    assert (signal.entry, signal.sl, signal.tps) == ((2370.0,), 2362.0, (2380.0,))
    assert "sell" not in normalise("سلام")


def test_a_question_is_not_a_signal() -> None:
    signal = parse("show my recent trades")
    assert signal.missing == ("symbol", "side", "stop loss", "target")
    assert not signal.complete
    assert parse("XAUUSD buy 2345 tp 2350").missing == ("stop loss",)


def test_broker_symbols_and_aliases() -> None:
    assert resolve_symbol("gold", BROKER) == "XAUUSD.r"
    assert resolve_symbol("eurusd", BROKER) == "EURUSD.r"
    assert resolve_symbol("usdjpy", BROKER) == ""
    assert resolve_symbol("signal") == ""
    assert resolve_symbol("audcad") == "AUDCAD"
    assert resolve_symbol("یورو", BROKER, {"یورو": "EURUSD"}) == "EURUSD.r"
    assert parse("Gold buy 2345 sl 2335 tp 2355", BROKER).symbol == "XAUUSD.r"


def test_the_prefilter_sorts_messages() -> None:
    cases = [
        ("XAUUSD BUY 2345-2340 SL 2335 TP1 2350", False, Kind.SIGNAL),
        ("XAUUSD TP1 hit ✅ +50 pips", False, Kind.UPDATE),
        ("TP2 خورد ✅", True, Kind.UPDATE),
        ("ریسک فری کنید", True, Kind.UPDATE),
        ("TP2 خورد ✅", False, Kind.NOISE),
        ("join our VIP channel now 50% off", False, Kind.NOISE),
        ("show my recent trades", False, Kind.NOISE),
        ("معاملات اخیر رو نشون بده", False, Kind.NOISE),
        ("gold looks bullish today, it will be great", False, Kind.NOISE),
        ("سلام دوستان صبح بخیر", False, Kind.NOISE),
        ("XAUUSD buy chart?", False, Kind.NOISE),
    ]
    found = [(text, classify(text, reply=reply)) for text, reply, _kind in cases]
    assert found == [(text, kind) for text, _reply, kind in cases]
    assert looks_like_signal("طلا فروش ۲۳۶۱ استاپ ۲۳۶۸ تارگت ۲۳۵۵")


def test_a_zone_around_the_price_is_a_market_order() -> None:
    order = plan(parse("XAUUSD BUY 2345-2340 SL 2335 TP1 2350 TP2 2360 TP3 open"), GOLD)
    assert order.ok, order.problems
    assert (order.order, order.entry, order.tps) == (OrderType.MARKET, 2343.3, (2350.0, 2360.0))
    assert order.notes  # the open target is left out and the card says so


def test_pending_orders_by_side() -> None:
    cases = {
        "XAUUSD buy 2338 sl 2330 tp 2350": (OrderType.LIMIT, 2338.0),
        "XAUUSD buy 2346 sl 2340 tp 2360": (OrderType.STOP, 2346.0),
        "XAUUSD sell 2350 sl 2356 tp 2340": (OrderType.LIMIT, 2350.0),
        "XAUUSD sell 2338 sl 2344 tp 2330": (OrderType.STOP, 2338.0),
        "XAUUSD buy 2338-2335 sl 2330 tp 2350": (OrderType.LIMIT, 2338.0),
        "XAUUSD sell 2338-2335 sl 2344 tp 2325": (OrderType.STOP, 2338.0),
    }
    for text, expected in cases.items():
        order = plan(parse(text), GOLD)
        assert order.ok, (text, order.problems)
        assert (order.order, order.entry) == expected, text


def test_a_price_close_to_the_market_is_taken_at_market() -> None:
    order = plan(parse("XAUUSD buy 2343.5 sl 2335 tp 2350"), GOLD)
    assert (order.order, order.entry, order.ok) == (OrderType.MARKET, 2343.3, True)


def test_wrong_prices_are_refused_with_the_reason() -> None:
    wrong_side = plan(parse("XAUUSD buy limit 2350 sl 2340 tp 2360"), GOLD)
    assert not wrong_side.ok
    assert wrong_side.problems == ("order side: a buy limit must be below the ask (2343.30)",)
    moved = plan(parse("GOLD sell now @ 2361 sl 2368 tp 2335"), GOLD)
    assert [p.split(":")[0] for p in moved.problems] == ["price has not moved"]
    far = plan(parse("XAUUSD buy 2355 sl 2350 tp 2365"), GOLD)
    assert [p.split(":")[0] for p in far.problems] == ["entry distance"]
    sl = plan(parse("XAUUSD buy 2338 sl 2340 tp 2350"), GOLD)
    assert "stop loss side" in sl.problems[0]
    tp = plan(parse("XAUUSD sell 2350 sl 2356 tp 2352"), GOLD)
    assert "targets side" in tp.problems[0]


def test_tight_stops_and_missing_data_block_and_low_rr_only_warns() -> None:
    tight = Market(bid=2343.0, ask=2343.3, atr=4.0, digits=2, point=0.01, stops_level=100)
    order = plan(parse("XAUUSD buy 2338 sl 2337.5 tp 2350"), tight)
    assert "stop distance" in " ".join(order.problems)
    closed = Market(bid=2343.0, ask=2343.3, atr=math.nan, digits=2, point=0.01, open=False)
    problems = plan(parse("XAUUSD buy 2338 sl 2330 tp 2350"), closed).problems
    names = [problem.split(":")[0] for problem in problems]
    assert names[:2] == ["market open", "ATR"]
    low = plan(parse("XAUUSD buy 2338 sl 2330 tp 2342"), GOLD)
    assert low.ok
    assert [c.name for c in low.checks if not c.passed] == ["reward to risk"]
    incomplete = plan(parse("XAUUSD buy 2338 tp 2342"), GOLD)
    assert incomplete.problems == ("complete signal: missing: stop loss",)


def test_legs_split_the_lots_and_merge_when_too_small() -> None:
    assert split(0.04, 3, 0.01, 0.01) == (0.02, 0.01, 0.01)
    assert split(0.1, 3, 0.01, 0.01) == (0.04, 0.03, 0.03)
    assert split(0.02, 3, 0.01, 0.01) == (0.01, 0.01)
    assert split(0.015, 2, 0.01, 0.01) == (0.01,)
    assert split(0.005, 2, 0.01, 0.01) == ()
    assert split(1.0, 2, 0.1, 0.1) == (0.5, 0.5)
    assert split(0.3, 2, 0.2, 0.1) == (0.3,)
