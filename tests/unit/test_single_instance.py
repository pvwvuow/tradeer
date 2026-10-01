import os
import subprocess
import sys
import tempfile
from pathlib import Path

from app.core.single_instance import InstanceLock

HOLD = """
import sys, time
sys.path.insert(0, sys.argv[2])
from pathlib import Path
from app.core.single_instance import InstanceLock
lock = InstanceLock(Path(sys.argv[1]))
print("locked" if lock.acquire() else "busy", flush=True)
time.sleep(float(sys.argv[3]))
"""


def try_in_other_process(path: Path, hold_seconds: float = 0.0) -> str:
    root = str(Path(__file__).resolve().parents[2])
    result = subprocess.run(
        [sys.executable, "-c", HOLD, str(path), root, str(hold_seconds)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return result.stdout.strip()


def test_a_second_process_cannot_take_the_same_profile() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "profile" / "instance.lock"
        with InstanceLock(path) as lock:
            assert lock.acquire()
            assert lock.held
            assert path.read_text(encoding="utf-8") == str(os.getpid())
            assert try_in_other_process(path) == "busy"
        assert not lock.held
        assert try_in_other_process(path) == "locked"


def test_releasing_twice_is_harmless() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        lock = InstanceLock(Path(tmp) / "instance.lock")
        assert lock.acquire()
        lock.release()
        lock.release()
        assert lock.acquire()
        lock.release()
