"""PC Velopack log of 5 and 6 October 2026: every update since 0.23.0 stopped at "Backing up
current dir" with "The process cannot access the file because it is being used by another
process", although no app process was left. MT5 had been started by the MetaTrader5 package
from the MT5 helper process and so worked in the app's `current` folder. The helper now works
from the user's home folder, so a terminal it starts never holds the app folder.
"""

from __future__ import annotations

import os

import pytest

from app.mt5 import terminal_process
from app.mt5.terminal_process import leave_app_folder


def test_the_helper_works_from_the_home_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    moves: list[str] = []
    monkeypatch.setattr(terminal_process.os, "chdir", moves.append)
    leave_app_folder()
    assert moves == [os.path.expanduser("~")]


def test_a_home_folder_that_cannot_be_entered_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(path: str) -> None:
        raise PermissionError(path)

    monkeypatch.setattr(terminal_process.os, "chdir", refuse)
    leave_app_folder()  # no error: the helper keeps working where it is


def test_the_helper_leaves_the_folder_before_it_says_ready() -> None:
    source = terminal_process.helper_main.__code__.co_names
    assert "leave_app_folder" in source
