from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.updates.backend import UpdateError, UpdateOffer
from app.updates.service import Command, UpdateService, UpdateStatus
from app.updates.settings import (
    UpdateSettings,
    UpdateSettingsSource,
    load_update_settings,
    save_update_settings,
)
from app.updates.state import CRASH_LAUNCHES, LaunchTracker, StartInfo
from app.updates.velopack_backend import NOT_INSTALLED, VelopackBackend
from app.updates.versions import is_major_update, is_newer, parse_version, tag_for
from tests.fakes.fake_updates import FakeBackend


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def start_info(previous: str = "", updated_from: str = "", crash: bool = False) -> StartInfo:
    return StartInfo("0.12.0", updated_from, previous, crash)


def service_for(
    tmp_path: Path,
    backend: FakeBackend,
    settings: UpdateSettings | None = None,
    start: StartInfo | None = None,
    clock: Clock | None = None,
) -> tuple[UpdateService, list[tuple[str, str]], list[tuple[str, str, str]]]:
    source = UpdateSettingsSource(tmp_path)
    if settings is not None:
        source.save(settings)
    logs: list[tuple[str, str]] = []
    records: list[tuple[str, str, str]] = []
    service = UpdateService(
        backend,
        source,
        start or start_info(),
        log=lambda level, message: logs.append((level, message)),
        record=lambda action, before, after: records.append((action, before, after)),
        clock=clock or Clock(),
        first_check=60.0,
    )
    return service, logs, records


def test_versions_parse_compare_and_tag() -> None:
    assert parse_version("v0.12.3") == parse_version("0.12.3-beta.1")
    assert parse_version("0.12") is None
    assert parse_version("x.1.2") is None
    assert is_newer("0.13.0", "0.12.9")
    assert not is_newer("0.12.0", "0.12.0")
    assert not is_newer("garbage", "0.12.0")
    assert is_major_update("1.0.0", "0.12.0")
    assert not is_major_update("0.13.0", "0.12.0")
    assert tag_for("0.12.0") == tag_for("v0.12.0") == "v0.12.0"


def test_settings_round_trip_and_bad_files(tmp_path: Path) -> None:
    assert load_update_settings(tmp_path) == (UpdateSettings(), "")
    save_update_settings(tmp_path, UpdateSettings(check_hours=12, paused=True))
    settings, note = load_update_settings(tmp_path)
    assert (settings.check_hours, settings.paused, note) == (12, True, "")
    (tmp_path / "updates.json").write_text('{"check_hours": 999}', encoding="utf-8")
    settings, note = load_update_settings(tmp_path)
    assert settings == UpdateSettings() and "invalid" in note
    (tmp_path / "updates.json").write_text("{", encoding="utf-8")
    assert "could not be read" in load_update_settings(tmp_path)[1]


def test_the_tracker_sees_an_update_and_remembers_the_previous_version(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    first = LaunchTracker("0.12.0", path).start()
    assert first == StartInfo("0.12.0", "", "", False)
    again = LaunchTracker("0.12.0", path).start()
    assert again.updated_from == "" and again.previous == ""
    updated = LaunchTracker("0.13.0", path).start()
    assert (updated.updated_from, updated.previous) == ("0.12.0", "0.12.0")
    later = LaunchTracker("0.13.0", path).start()
    assert (later.updated_from, later.previous) == ("", "0.12.0")


def test_two_early_ends_in_a_row_after_an_update_are_a_crash_loop(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    clock = Clock()
    LaunchTracker("0.12.0", path, clock).start()
    for _ in range(CRASH_LAUNCHES):
        clock.now += 1
        info = LaunchTracker("0.13.0", path, clock).start()
        assert not info.crash_loop  # not yet: the earlier launches are counted, not this one
    clock.now += 1
    assert LaunchTracker("0.13.0", path, clock).start().crash_loop
    clock.now += 1
    healthy = LaunchTracker("0.13.0", path, clock)
    healthy.start()
    healthy.mark_healthy()
    clock.now += 1
    assert not LaunchTracker("0.13.0", path, clock).start().crash_loop


def test_a_damaged_state_file_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("[1, 2", encoding="utf-8")
    assert LaunchTracker("0.12.0", path).start() == StartInfo("0.12.0", "", "", False)


def test_an_automatic_check_downloads_a_minor_update(tmp_path: Path) -> None:
    backend = FakeBackend()
    backend.offer = UpdateOffer("0.13.0", notes="## Changes", size=3 * 1024 * 1024, delta=True)
    service, logs, _ = service_for(tmp_path, backend)
    seen: list[UpdateStatus] = []
    service.add_listener(lambda snapshot: seen.append(snapshot.status))
    service.handle(Command.AUTO_CHECK)
    snapshot = service.snapshot
    assert snapshot.status is UpdateStatus.READY and snapshot.version == "0.13.0"
    assert snapshot.delta and snapshot.notes == "## Changes" and snapshot.progress == 100
    assert backend.downloaded == ["0.13.0"]
    assert UpdateStatus.DOWNLOADING in seen and UpdateStatus.AVAILABLE in seen
    assert any("3.0 MB, changes only" in message for _, message in logs)


def test_a_major_update_is_never_downloaded_by_itself(tmp_path: Path) -> None:
    backend = FakeBackend()
    backend.offer = UpdateOffer("1.0.0", size=10)
    service, _, _ = service_for(tmp_path, backend)
    service.handle(Command.AUTO_CHECK)
    assert service.snapshot.status is UpdateStatus.AVAILABLE and service.snapshot.major
    assert backend.downloaded == []
    service.handle(Command.DOWNLOAD)
    assert service.snapshot.ready and backend.downloaded == ["1.0.0"]


def test_no_background_download_when_switched_off(tmp_path: Path) -> None:
    backend = FakeBackend()
    backend.offer = UpdateOffer("0.13.0")
    service, _, _ = service_for(tmp_path, backend, UpdateSettings(auto_download=False))
    service.handle(Command.AUTO_CHECK)
    assert service.snapshot.status is UpdateStatus.AVAILABLE and backend.downloaded == []


def test_up_to_date_and_an_older_feed_offer_nothing(tmp_path: Path) -> None:
    backend = FakeBackend()
    service, _, _ = service_for(tmp_path, backend)
    service.handle(Command.CHECK)
    assert service.snapshot.status is UpdateStatus.UP_TO_DATE
    assert service.snapshot.last_check is not None
    backend.offer = UpdateOffer("0.11.0")
    service.handle(Command.CHECK)
    assert service.snapshot.status is UpdateStatus.UP_TO_DATE


def test_a_failed_automatic_check_is_quiet_and_a_manual_one_says_why(tmp_path: Path) -> None:
    backend = FakeBackend()
    backend.fail_check = True
    service, logs, _ = service_for(tmp_path, backend)
    service.handle(Command.AUTO_CHECK)
    assert service.snapshot.status is UpdateStatus.IDLE
    assert "automatic check failed" in service.snapshot.message
    assert logs[-1][0] == "WARNING"
    service.handle(Command.CHECK)
    assert service.snapshot.status is UpdateStatus.ERROR
    assert "no internet" in service.snapshot.message


def test_a_failed_download_is_an_error_and_nothing_is_ready(tmp_path: Path) -> None:
    backend = FakeBackend()
    backend.offer = UpdateOffer("0.13.0")
    backend.fail_download = True
    service, _, _ = service_for(tmp_path, backend)
    service.handle(Command.AUTO_CHECK)
    assert service.snapshot.status is UpdateStatus.ERROR
    assert "checksum mismatch" in service.snapshot.message
    assert service.apply() == "No downloaded update is waiting."
    assert backend.applied == []


def test_apply_hands_the_ready_version_to_the_installer(tmp_path: Path) -> None:
    backend = FakeBackend()
    backend.offer = UpdateOffer("0.13.0")
    service, _, records = service_for(tmp_path, backend)
    service.handle(Command.AUTO_CHECK)
    assert service.apply() == ""
    assert backend.applied == ["0.13.0"]
    assert records[-1] == ("update started", "0.12.0", "0.13.0")


def test_checks_follow_the_schedule_and_the_pause(tmp_path: Path) -> None:
    clock = Clock()
    backend = FakeBackend()
    service, _, _ = service_for(tmp_path, backend, clock=clock)
    assert not service.due()
    clock.now += 61
    assert service.due()
    service.handle(Command.AUTO_CHECK)
    assert not service.due()
    clock.now += 6 * 3600
    assert service.due()
    source = UpdateSettingsSource(tmp_path)
    source.save(UpdateSettings(paused=True))
    paused, _, _ = service_for(tmp_path, backend, clock=clock)
    clock.now += 10 * 3600
    assert not paused.due() and paused.snapshot.paused


def test_rollback_downloads_the_previous_version(tmp_path: Path) -> None:
    backend = FakeBackend(current="0.13.0")
    backend.versions["0.12.0"] = UpdateOffer("0.12.0", size=50)
    start = StartInfo("0.13.0", "", "0.12.0", True)
    service, _, records = service_for(tmp_path, backend, start=start)
    assert service.snapshot.crash_loop and service.snapshot.rollback_to == "0.12.0"
    assert "did not start properly" in service.snapshot.message
    service.handle(Command.ROLLBACK)
    snapshot = service.snapshot
    assert snapshot.ready and snapshot.downgrade and snapshot.version == "0.12.0"
    assert service.apply() == ""
    assert backend.applied == ["0.12.0"] and records[-1][0] == "rollback started"


def test_a_missing_rollback_version_is_an_error(tmp_path: Path) -> None:
    backend = FakeBackend(current="0.13.0")
    service, _, _ = service_for(tmp_path, backend, start=StartInfo("0.13.0", "", "0.12.0", False))
    service.handle(Command.ROLLBACK)
    assert service.snapshot.status is UpdateStatus.ERROR
    assert "0.12.0 was not found" in service.snapshot.message


def test_the_first_run_after_an_update_is_logged_and_audited(tmp_path: Path) -> None:
    backend = FakeBackend(current="0.13.0")
    start = StartInfo("0.13.0", "0.12.0", "0.12.0", False)
    service, logs, records = service_for(tmp_path, backend, start=start)
    assert service.snapshot.message == "Updated from 0.12.0 to 0.13.0."
    assert records == [("update installed", "0.12.0", "0.13.0")]
    assert ("INFO", "Update installed: 0.12.0 -> 0.13.0") in logs


def test_without_an_install_the_service_says_why_and_never_starts(tmp_path: Path) -> None:
    backend = FakeBackend(reason=NOT_INSTALLED)
    service, _, _ = service_for(tmp_path, backend)
    assert service.snapshot.status is UpdateStatus.UNAVAILABLE
    service.start()
    service.handle(Command.CHECK)
    assert service.snapshot.status is UpdateStatus.UNAVAILABLE
    service.stop()


def test_the_worker_thread_runs_commands_and_stops(tmp_path: Path) -> None:
    backend = FakeBackend()
    backend.offer = UpdateOffer("0.13.0")
    service, _, _ = service_for(tmp_path, backend, UpdateSettings(auto_download=False))
    service.start()
    service.check_now()
    service.stop(timeout=5.0)
    assert service.snapshot.status in (UpdateStatus.AVAILABLE, UpdateStatus.IDLE)


# ----- the Velopack adapter on a fake module -----


class Asset:
    def __init__(self, version: str, size: int, notes: str = "") -> None:
        self.Version = version
        self.Size = size
        self.NotesMarkdown = notes


class Info:
    def __init__(self, target: Asset, deltas: list[Asset], downgrade: bool = False) -> None:
        self.TargetFullRelease = target
        self.DeltasToTarget = deltas
        self.IsDowngrade = downgrade


class FakeManager:
    installed = True
    info: Info | None = None
    calls: list[tuple[str, Any]] = []

    def __init__(self, source: object, options: object = None) -> None:
        if not FakeManager.installed:
            raise RuntimeError("This application is not installed")
        self.source = source
        self.options = options
        FakeManager.calls.append(("new", source))

    def get_current_version(self) -> str:
        return "0.12.0"

    def check_for_updates(self) -> Info | None:
        return FakeManager.info

    def download_updates(self, info: Info, progress: Callable[[int], None]) -> None:
        progress(100)
        FakeManager.calls.append(("download", info))

    def wait_exit_then_apply_updates(self, info: Info) -> None:
        FakeManager.calls.append(("apply", info))


class FakeVelopack:
    UpdateManager = FakeManager

    @staticmethod
    def UpdateOptions(downgrade: bool, deltas: int) -> tuple[bool, int]:  # noqa: N802
        return (downgrade, deltas)


class FakeGithubVelopack(FakeVelopack):
    @staticmethod
    def GithubSource(url: str) -> str:  # noqa: N802
        return f"github:{url}"


def reset_manager() -> None:
    FakeManager.installed = True
    FakeManager.info = None
    FakeManager.calls = []


def test_velopack_offers_the_delta_size_and_applies_after_exit() -> None:
    reset_manager()
    backend = VelopackBackend("https://github.com/o/r", FakeGithubVelopack())
    assert backend.unavailable_reason() == "" and backend.current_version() == "0.12.0"
    assert FakeManager.calls[0] == ("new", "github:https://github.com/o/r")
    assert backend.check() is None
    FakeManager.info = Info(Asset("0.13.0", 90_000_000, "notes"), [Asset("0.13.0", 2_000)])
    offer = backend.check()
    assert offer is not None
    assert (offer.version, offer.size, offer.delta, offer.notes) == ("0.13.0", 2_000, True, "notes")
    seen: list[int] = []
    backend.download(offer, seen.append)
    backend.apply_after_exit(offer)
    assert seen == [100]
    assert [name for name, _ in FakeManager.calls[-2:]] == ["download", "apply"]


def test_velopack_without_github_source_uses_the_latest_release_files() -> None:
    reset_manager()
    VelopackBackend("https://github.com/o/r/", FakeVelopack())
    assert FakeManager.calls[0] == ("new", "https://github.com/o/r/releases/latest/download")


def test_velopack_rollback_reads_that_release_and_allows_a_downgrade() -> None:
    reset_manager()
    backend = VelopackBackend("https://github.com/o/r", FakeVelopack())
    FakeManager.info = Info(Asset("0.11.0", 80_000_000), [], downgrade=True)
    offer = backend.check_version("0.11.0")
    assert offer is not None and offer.downgrade and not offer.delta and offer.size == 80_000_000
    assert FakeManager.calls[-1] == ("new", "https://github.com/o/r/releases/download/v0.11.0")


def test_velopack_outside_an_install_explains_itself() -> None:
    reset_manager()
    FakeManager.installed = False
    backend = VelopackBackend("https://github.com/o/r", FakeVelopack())
    assert backend.unavailable_reason().startswith(NOT_INSTALLED)
    with pytest.raises(UpdateError):
        backend.check()


def test_an_offer_from_elsewhere_cannot_be_applied() -> None:
    reset_manager()
    backend = VelopackBackend("https://github.com/o/r", FakeVelopack())
    with pytest.raises(UpdateError):
        backend.apply_after_exit(UpdateOffer("0.13.0"))
