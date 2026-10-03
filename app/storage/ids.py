"""Client-generated ids (spec D3.6): every row gets its UUID on this PC.

Rows for things MT5 already identifies (an account, a position) get a deterministic UUID, so
importing the same history twice, or on another PC, gives the same ids and the cloud upsert
can never create a duplicate.
"""

from __future__ import annotations

import uuid

NAMESPACE = uuid.UUID("6f2b8c1e-5d0a-4e7b-9a43-2c1d9e8f7a60")


def new_id() -> str:
    return str(uuid.uuid4())


def stable_id(*parts: object) -> str:
    """The same parts always give the same UUID (version 5)."""
    return str(uuid.uuid5(NAMESPACE, "/".join(str(part) for part in parts)))


def account_id(server: str, login: int) -> str:
    return stable_id("account", server.strip().casefold(), int(login))


def trade_id(account: str, position_id: int) -> str:
    return stable_id("trade", account, int(position_id))
