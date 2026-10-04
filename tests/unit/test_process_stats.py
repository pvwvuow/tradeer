"""This process's CPU and memory (spec E3): the meter's arithmetic and the readers."""

from pathlib import Path

from app.observability.process_stats import (
    CpuMeter,
    cpu_seconds,
    memory_bytes,
    statm_memory,
)


class Ticks:
    def __init__(self, values: list[float]) -> None:
        self.values = values

    def __call__(self) -> float:
        return self.values.pop(0)


def test_the_cpu_meter_is_a_share_of_the_whole_pc() -> None:
    meter = CpuMeter(Ticks([0.0, 0.5, 1.5]), Ticks([0.0, 10.0, 20.0]), cores=1)
    assert meter.sample() is None  # the first sample only starts the clock
    assert meter.sample() == 5.0
    assert meter.sample() == 7.5  # the average of 5 % and 10 %
    quad = CpuMeter(Ticks([0.0, 0.4]), Ticks([0.0, 10.0]), cores=4)
    quad.sample()
    assert quad.sample() == 1.0


def test_the_cpu_meter_keeps_the_last_readings_only() -> None:
    cpu = [0.0, 1.0, 1.0, 1.0]
    meter = CpuMeter(Ticks(cpu), Ticks([0.0, 10.0, 20.0, 30.0]), cores=1, samples=2)
    meter.sample()
    assert meter.sample() == 10.0
    assert meter.sample() == 5.0
    assert meter.sample() == 0.0
    assert cpu_seconds() >= 0.0


def test_statm_is_read_in_pages(tmp_path: Path) -> None:
    good = tmp_path / "statm"
    good.write_text("1000 25 10 1 0 30 0\n", encoding="ascii")
    found = statm_memory(good)
    assert found is not None and found >= 25 * 4096 / 4  # whatever the page size is
    broken = tmp_path / "broken"
    broken.write_text("nope", encoding="ascii")
    assert statm_memory(broken) is None
    assert statm_memory(tmp_path / "missing") is None


def test_this_process_memory_is_readable_on_windows_and_linux() -> None:
    found = memory_bytes()
    assert found is not None and found > 1_000_000  # a Python process uses more than 1 MB
