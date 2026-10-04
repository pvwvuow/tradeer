"""Health checks in the synced table (spec E2)."""

from app.observability.health import HealthCheck, HealthStatus
from app.storage.health_store import HealthRepository
from tests.unit.storage_helpers import temporary_store

NOW = 1_790_000_000.0


def test_checks_are_saved_and_the_problems_read_back() -> None:
    checks = [
        HealthCheck("disk_space", "Disk space", HealthStatus.OK, "80 GB free", 80.0, "GB"),
        HealthCheck("algo_trading", "Algo Trading on", HealthStatus.WARNING, "It is off", fix="x"),
    ]
    with temporary_store() as store:
        repository = HealthRepository(store, lambda: None)
        assert repository.record(checks, NOW) == 2
        assert repository.record(checks[1:], NOW + 60) == 1
        everything = repository.recent()
        problems = repository.recent(problems_only=True)
        assert store.count("health_checks") == 3
        assert store.outbox_counts().pending >= 3
    assert [item.name for item in problems] == ["algo_trading", "algo_trading"]
    assert problems[0].time == NOW + 60 and problems[0].text == "It is off"
    assert {item.status for item in everything} == {"ok", "warning"}
    disk = next(item for item in everything if item.name == "disk_space")
    assert disk.value == 80.0
