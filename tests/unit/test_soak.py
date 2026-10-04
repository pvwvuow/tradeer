"""The soak test report (spec G3 phase 14): the run, the budgets and the Markdown."""

from typing import Any

from app.observability.soak import (
    DAY,
    Sample,
    SoakInputs,
    SoakReport,
    evaluate_soak,
    find_run,
    report_name,
    slope_per_day,
    soak_markdown,
)

START = 1_790_000_000.0
STEP = 900.0


def times(hours: float) -> list[float]:
    return [START + index * STEP for index in range(int(hours * 3600 / STEP) + 1)]


def samples(
    hours: float = 26.0,
    *,
    memory: float = 260.0,
    growth: float = 0.0,
    cpu: float = 0.8,
    mt5: float = 15.0,
    bar_status: str = "ok",
) -> list[Sample]:
    found: list[Sample] = []
    for moment in times(hours):
        days = (moment - START) / DAY
        found += [
            Sample(moment, "memory_mb", memory + growth * days),
            Sample(moment, "cpu_percent", cpu),
            Sample(moment, "mt5_ms_p95", mt5),
            Sample(moment, "bar_ms_p95", 250.0, bar_status),
        ]
    return found


def report(run: list[Sample] | None = None, **changes: Any) -> SoakReport:
    found = run if run is not None else samples()
    start, end, gap = find_run([sample.time for sample in found])
    values: dict[str, Any] = {"start": start, "end": end, "samples": found, "longest_gap": gap}
    values.update(changes)
    return evaluate_soak(SoakInputs(**values))


def failed(found: SoakReport) -> list[str]:
    return [check.name for check in found.checks if not check.passed]


def test_the_latest_continuous_run_is_found() -> None:
    earlier = [START - 3 * DAY, START - 3 * DAY + STEP]
    restart = [START + 10 * STEP + 120.0]  # an update restart is no gap
    start, end, gap = find_run([*earlier, *times(26.0), *restart])
    assert start == START and end == START + 26 * 3600 and gap == STEP
    assert find_run([]) == (0.0, 0.0, 0.0)


def test_a_quiet_day_on_demo_passes() -> None:
    found = report()
    assert found.passed, found.checks
    assert found.summary == "Soak test PASS: 7 of 7 checks passed over 26.0 hours"
    assert [row[0] for row in found.rows] == [
        "Memory",
        "CPU",
        "MT5 call p95",
        "Closed-bar processing p95",
    ]


def test_every_budget_can_fail() -> None:
    assert failed(report(samples(12.0))) == ["Duration"]
    assert failed(report(samples(memory=620.0))) == ["Memory"]
    assert failed(report(samples(growth=80.0))) == ["No memory leak"]
    assert failed(report(samples(cpu=4.5))) == ["CPU"]
    assert failed(report(samples(mt5=1500.0))) == ["MT5 calls"]
    assert failed(report(samples(bar_status="warning"))) == ["Bar processing"]
    assert failed(report(crash_reports=1)) == ["Stability"]
    shaky = report(critical_logs=2, frozen_workers=1, disconnects=3, error_logs=4)
    assert failed(shaky) == ["Stability"]
    assert "2 CRITICAL lines" in shaky.checks[-1].value
    assert "lost 3 time(s)" in shaky.notes[1] and shaky.notes[0].startswith("4 ERROR")


def test_nothing_saved_fails_plainly() -> None:
    empty = evaluate_soak(SoakInputs(start=0.0, end=0.0))
    assert not empty.passed and empty.hours == 0.0
    memory = next(check for check in empty.checks if check.name == "Memory")
    assert memory.value == "not measured"
    assert empty.rows[0] == ("Memory", "0", "n/a", "n/a", "n/a")


def test_the_memory_slope() -> None:
    slope = slope_per_day([(0.0, 0.0), (DAY / 2, 5.0), (DAY, 10.0)])
    assert slope is not None and abs(slope - 10.0) < 1e-9
    assert slope_per_day([(0.0, 1.0), (DAY, 2.0)]) is None
    assert slope_per_day([(5.0, 1.0), (5.0, 2.0), (5.0, 3.0)]) is None


def test_the_markdown_report() -> None:
    text = soak_markdown(report(), START + 27 * 3600, "0.19.0")
    assert text.startswith("# Soak test report")
    assert "by MT5 Trading Workstation 0.19.0" in text
    assert "| Memory | PASS | 260 MB at most | < 500 MB |" in text
    assert "## Samples" in text and "| CPU | 105 |" in text
    assert report_name(START) == "soak-20260921-141320.md"
