"""Composition root for the Phase 1 desktop shell."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow


def main() -> int:
    if "--self-check" in sys.argv:
        print("MT5 Trading Workstation self-check: foundation import OK")
        return 0
    application = QApplication(sys.argv)
    application.setApplicationName("MT5 Trading Workstation")
    window = MainWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
