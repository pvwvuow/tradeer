"""Soak runs that add up (asked for on 6 October 2026): the PC cannot stay on overnight, so
the newest runs of 4 hours or more are added up to the 24 hours, each checked for a leak."""

from app.observability.soak import (
    DAY,
    HOUR,
    Sample,
    SoakInputs,
    evaluate_soak,
    find_runs,
    pick_runs,
    soak_markdown,
)

START = 1_790_000_000.0
STEP = 900.0


def run(start: float, hours: float, growth: float = 0.0) -> list[Sample]:
    found: list[Sample] = []
    steps = int(hours * HOUR / STEP)
    for index in range(steps + 1):
        moment = start + index * STEP
        memory = 260.0 + growth * (moment - start) / DAY
        found += [
            Sample(moment, "memory_mb", memory),
            Sample(moment, "cpu_percent", 0.8),
            Sample(moment, "mt5_ms_p95", 15.0),
            Sample(moment, "bar_ms_p95", 250.0, "ok"),
        ]
    return found


def evenings(growth: float = 0.0) -> list[Sample]:
    """Three evenings of 9 hours, one day apart, and a short morning run."""
    found: list[Sample] = []
    for day in range(3):
        found += run(START + day * DAY, 9.0, growth)
    found += run(START + 2 * DAY + 12 * HOUR, 1.0)  # 1 h: left out
    return found


def inputs(samples: list[Sample]) -> SoakInputs:
    runs = pick_runs(find_runs([sample.time for sample in samples]))
    return SoakInputs(
        start=runs[0][0],
        end=runs[-1][1],
        samples=samples,
        longest_gap=max(gap for _, _, gap in runs),
        runs=tuple((start, end) for start, end, _ in runs),
    )


def test_runs_are_split_at_gaps() -> None:
    found = find_runs([START, START + STEP, START + 3 * HOUR, START + 3 * HOUR + STEP])
    assert found == [(START, START + STEP, STEP), (START + 3 * HOUR, START + 3 * HOUR + STEP, STEP)]
    assert find_runs([]) == []


def test_three_evenings_add_up_to_a_pass() -> None:
    report = evaluate_soak(inputs(evenings()))
    assert report.passed, report.checks
    assert len(report.runs) == 3 and report.hours == 27.0
    assert report.summary == "Soak test PASS: 7 of 7 checks passed over 27.0 hours"
    assert report.checks[0].value.startswith("27.0 hours in 3 run(s)")
    text = soak_markdown(report, START + 3 * DAY)
    assert text.count(" h)") == 3 and "(27.0 hours in all)" in text


def test_a_leak_in_one_run_fails() -> None:
    leaky = evenings()[:-4 * 5] + run(START + 2 * DAY + 12 * HOUR, 1.0)
    leaky = [s for s in leaky if not (START + 2 * DAY <= s.time <= START + 2 * DAY + 9 * HOUR)]
    leaky += run(START + 2 * DAY, 9.0, growth=120.0)
    report = evaluate_soak(inputs(leaky))
    assert [check.name for check in report.checks if not check.passed] == ["No memory leak"]
    assert "(worst run)" in report.checks[2].value


def test_two_evenings_are_not_enough() -> None:
    samples = run(START, 9.0) + run(START + DAY, 9.0)
    report = evaluate_soak(inputs(samples))
    assert [check.name for check in report.checks if not check.passed] == ["Duration"]


def test_without_a_long_enough_run_the_latest_is_reported() -> None:
    samples = run(START, 1.0) + run(START + DAY, 2.0)
    runs = pick_runs(find_runs([sample.time for sample in samples]))
    assert [(start, end) for start, end, _ in runs] == [(START + DAY, START + DAY + 2 * HOUR)]
