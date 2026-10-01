"""Account profiles (spec C1): one folder per profile with `account.json`.

The password is never stored here; it lives in Windows Credential Manager (`credentials.py`).
Run one app instance per profile (`--profile NAME`) to use several accounts at the same time.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.mt5.checklist import DEFAULT_SYMBOLS, ConnectRequest

ACCOUNT_FILE_NAME = "account.json"
MARKER_FILES = (ACCOUNT_FILE_NAME, "ui_prefs.json")


def _default_symbols() -> list[str]:
    return list(DEFAULT_SYMBOLS)


class AccountProfile(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    login: int | None = Field(default=None, gt=0)
    server: str = ""
    terminal_path: str = ""
    symbols: list[str] = Field(default_factory=_default_symbols, min_length=1, max_length=10)
    auto_connect: bool = True

    @property
    def configured(self) -> bool:
        return self.login is not None and bool(self.server)

    def request(self, password: str = "") -> ConnectRequest:
        return ConnectRequest(
            terminal_path=self.terminal_path,
            login=self.login,
            password=password,
            server=self.server,
            symbols=tuple(self.symbols),
        )


def load_account(directory: Path) -> AccountProfile:
    try:
        raw = json.loads((directory / ACCOUNT_FILE_NAME).read_text(encoding="utf-8"))
        return AccountProfile.model_validate(raw)
    except (OSError, ValueError):
        return AccountProfile()


def save_account(directory: Path, account: AccountProfile) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / ACCOUNT_FILE_NAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(account.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(target)


def list_profiles(root: Path) -> list[str]:
    """Profiles that were used before, sorted by name."""
    try:
        folders = [entry for entry in root.iterdir() if entry.is_dir()]
    except OSError:
        return []
    used = [
        folder.name
        for folder in folders
        if any((folder / name).exists() for name in MARKER_FILES)
    ]
    return sorted(used, key=str.casefold)
