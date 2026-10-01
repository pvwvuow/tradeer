import os
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from app.observability.files import ALL_LOG_NAME, LogFilePolicy, LogFileWriter

KEEP_EVERYTHING = LogFilePolicy(retention_days=100_000)


class FakeClock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


def local(day: int, hour: int = 12, minute: int = 0, month: int = 9) -> datetime:
    return datetime(2026, month, day, hour, minute).astimezone()


def set_mtime(path: Path, moment: datetime) -> None:
    os.utime(path, (moment.timestamp(), moment.timestamp()))


def zip_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        assert len(names) == 1
        return archive.read(names[0]).decode("utf-8")


def test_lines_go_to_their_category_file_and_to_all_log() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "logs"
        with LogFileWriter(root, KEEP_EVERYTHING, clock=FakeClock(local(20))) as writer:
            writer.write("mt5", '{"n":1}', "mt5 line")
            writer.write("risk", '{"n":2}', "risk line")
            writer.write("mt5", '{"n":3}', "second mt5 line")
            assert len(writer.open_paths()) == 3
        mt5_text = (root / "mt5" / "2026-09-20.jsonl").read_text(encoding="utf-8")
        assert mt5_text == '{"n":1}\n{"n":3}\n'
        assert (root / "risk" / "2026-09-20.jsonl").read_text(encoding="utf-8") == '{"n":2}\n'
        all_log = (root / ALL_LOG_NAME).read_text(encoding="utf-8")
        assert all_log == "mt5 line\nrisk line\nsecond mt5 line\n"


def test_unsafe_category_names_cannot_escape_the_log_folder() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "logs"
        with LogFileWriter(root, KEEP_EVERYTHING, clock=FakeClock(local(20))) as writer:
            writer.write("../evil", "{}", "line")
        assert (root / "app" / "2026-09-20.jsonl").exists()
        assert not (Path(tmp) / "evil").exists()


def test_files_rotate_and_compress_when_the_date_changes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "logs"
        clock = FakeClock(local(20, 23, 59))
        with LogFileWriter(root, KEEP_EVERYTHING, clock=clock) as writer:
            writer.write("app", '{"day":20}', "day 20")
            clock.moment = local(21, 0, 1)
            writer.write("app", '{"day":21}', "day 21")
        archives = sorted((root / "app").glob("2026-09-20.*.jsonl.zip"))
        assert len(archives) == 1
        assert zip_text(archives[0]) == '{"day":20}\n'
        assert (root / "app" / "2026-09-21.jsonl").read_text(encoding="utf-8") == '{"day":21}\n'
        assert not (root / "app" / "2026-09-20.jsonl").exists()
        all_archives = list(root.glob("all.*.log.zip"))
        assert len(all_archives) == 1
        assert zip_text(all_archives[0]) == "day 20\n"
        assert (root / ALL_LOG_NAME).read_text(encoding="utf-8") == "day 21\n"


def test_files_rotate_and_compress_when_they_grow_too_big() -> None:
    policy = LogFilePolicy(max_file_bytes=40, retention_days=100_000)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "logs"
        with LogFileWriter(root, policy, clock=FakeClock(local(20))) as writer:
            for index in range(3):
                writer.write("perf", "x" * 25 + str(index), "y" * 25 + str(index))
        assert len(list((root / "perf").glob("*.zip"))) == 2
        assert (root / "perf" / "2026-09-20.jsonl").read_text(encoding="utf-8") == "x" * 25 + "2\n"
        assert len(list(root.glob("all.*.log.zip"))) == 2


def test_leftovers_from_earlier_runs_are_compressed_but_today_is_kept_open() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "logs"
        (root / "app").mkdir(parents=True)
        old = root / "app" / "2026-09-18.jsonl"
        old.write_text('{"old":true}\n', encoding="utf-8")
        today = root / "app" / "2026-09-20.jsonl"
        today.write_text('{"earlier":true}\n', encoding="utf-8")
        all_log = root / ALL_LOG_NAME
        all_log.write_text("yesterday\n", encoding="utf-8")
        set_mtime(all_log, local(19))
        with LogFileWriter(root, KEEP_EVERYTHING, clock=FakeClock(local(20))) as writer:
            writer.write("risk", "{}", "today")
        assert not old.exists()
        assert len(list((root / "app").glob("2026-09-18.*.jsonl.zip"))) == 1
        assert today.read_text(encoding="utf-8") == '{"earlier":true}\n'
        assert all_log.read_text(encoding="utf-8") == "today\n"
        assert zip_text(next(root.glob("all.*.log.zip"))) == "yesterday\n"


def test_retention_deletes_old_closed_files_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "logs"
        (root / "app").mkdir(parents=True)
        expired = root / "app" / "2026-08-01.20260802-000000-000000.jsonl.zip"
        recent = root / "app" / "2026-09-15.20260916-000000-000000.jsonl.zip"
        for path in (expired, recent):
            path.write_bytes(b"zip")
        set_mtime(expired, local(1, month=8))
        set_mtime(recent, local(15))
        clock = FakeClock(local(20))
        with LogFileWriter(root, LogFilePolicy(retention_days=30), clock=clock) as writer:
            writer.write("app", "{}", "line")
        assert not expired.exists()
        assert recent.exists()


def test_size_cap_deletes_the_oldest_closed_files_first() -> None:
    policy = LogFilePolicy(max_total_bytes=1000, retention_days=100_000)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "logs"
        (root / "app").mkdir(parents=True)
        archives = []
        for day in (10, 11, 12):
            path = root / "app" / f"2026-09-{day}.20260901-000000-000000.jsonl.zip"
            path.write_bytes(b"z" * 400)
            set_mtime(path, local(day))
            archives.append(path)
        unrelated = root / "notes.txt"
        unrelated.write_text("not a log file", encoding="utf-8")
        with LogFileWriter(root, policy, clock=FakeClock(local(20))) as writer:
            writer.write("app", "{}", "line")
            assert all(path.exists() for path in writer.open_paths())
        assert [path.exists() for path in archives] == [False, True, True]
        assert unrelated.exists()


def test_a_closed_writer_ignores_further_lines() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "logs"
        writer = LogFileWriter(root, KEEP_EVERYTHING, clock=FakeClock(local(20)))
        writer.close()
        writer.write("app", "{}", "line")
        assert not root.exists()
        assert writer.failed_writes == 0
