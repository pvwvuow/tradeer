"""Composition root: parse arguments, run the self-check, or start the desktop app."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from app.cli import parse_args, run_self_check, self_check_main
from app.core.paths import app_data_dir
from app.core.ui_prefs import load_prefs

APP_NAME = "MT5 Trading Workstation"


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    options = parse_args(args)
    if options.self_check:
        return self_check_main(options)
    return run_gui(options.profile, args)


def run_gui(profile: str, qt_args: list[str]) -> int:
    # Qt is imported lazily so that `--self-check` can report a broken Qt install.
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QMessageBox

    from app.ui.main_window import MainWindow

    application = QApplication([sys.argv[0], *qt_args])
    application.setApplicationName(APP_NAME)
    font = QFont()
    font.setFamilies(["Inter", "Segoe UI Variable", "Segoe UI"])
    application.setFont(font)
    if getattr(sys, "frozen", False):
        ok, report = run_self_check()
        if not ok:
            # A production build must fail loudly, never fall back to fake data (spec I2).
            QMessageBox.critical(None, APP_NAME, report)
            return 1
    prefs_dir = app_data_dir(profile)
    window = MainWindow(load_prefs(prefs_dir), prefs_dir)
    window.show()
    return int(application.exec())


if __name__ == "__main__":
    raise SystemExit(main())
