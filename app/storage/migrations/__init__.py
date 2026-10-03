"""Schema migrations, applied in order by `app.storage.migrate`.

Never edit a migration after it was released: the checksum check refuses a changed one. Add a
new module `mNNNN_name.py` with a `SQL` string and list it below. Listing the modules
explicitly also makes PyInstaller bundle them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.storage.migrations import m0001_initial


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


MIGRATIONS: tuple[Migration, ...] = (Migration(1, "initial", m0001_initial.SQL),)
