"""A stop inside the price's normal moves only warns (0.51.1)."""

from app.signals.parse import parse
from app.signals.plan import Market, plan

CABLE = Market(bid=1.32334, ask=1.32341, atr=0.00051)


def test_a_tiny_stop_warns_but_does_not_block() -> None:
    order = plan(parse("GBPUSD sell 1.3235 sl 1.3237 tp 1.3231"), CABLE)
    noise = [check for check in order.checks if check.name == "stop size"]
    assert len(noise) == 1 and noise[0].warning and not noise[0].passed
    assert "stop size" not in " ".join(order.problems)


def test_a_normal_stop_passes_the_noise_check() -> None:
    order = plan(parse("GBPUSD sell 1.3235 sl 1.3250 tp 1.3200"), CABLE)
    noise = [check for check in order.checks if check.name == "stop size"]
    assert len(noise) == 1 and noise[0].passed
    assert order.ok, order.problems
