"""Velopack's start-up hook (install, update and uninstall events). It must run first in the
main process, right after `multiprocessing.freeze_support()`, and never stops the app."""

from __future__ import annotations

import importlib


def run_velopack_hooks() -> None:
    try:
        velopack = importlib.import_module("velopack")
    except Exception:
        return
    try:
        velopack.App().run()
    except Exception:
        return
