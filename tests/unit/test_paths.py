import os
import tempfile
from pathlib import Path
from unittest import mock

from app.core.paths import app_data_dir, crash_reports_dir, logs_dir


def test_logs_and_crash_reports_live_in_the_profile_folder() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.dict(os.environ, {"APPDATA": tmp, "XDG_CONFIG_HOME": tmp}):
            profile_dir = app_data_dir("demo-1")
            assert logs_dir("demo-1") == profile_dir / "logs"
            assert crash_reports_dir("demo-1") == profile_dir / "crash_reports"
        assert Path(tmp) in profile_dir.parents
