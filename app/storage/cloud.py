"""Cloud settings and sign-in (spec D5): Supabase Auth with email, never the service key.

The project URL, the anon key and the email live in `cloud.json` in the profile folder. The
password is only used to sign in and is never saved. The refresh token is kept in Windows
Credential Manager, so the app stays signed in after a restart.
"""

from __future__ import annotations

import base64
import contextlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from app.core.credentials import CredentialError, CredentialStore
from app.core.paths import safe_profile_name
from app.observability.masking import MASKER
from app.storage.remote import AuthSession
from app.storage.sync import SyncEngine

CLOUD_FILE_NAME = "cloud.json"
LOCAL_HOSTS = ("http://localhost", "http://127.0.0.1")


class CloudSetupError(ValueError):
    """The cloud settings cannot be used (shown to the user as is)."""


class CloudSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str = ""
    anon_key: str = ""
    email: str = ""
    enabled: bool = False

    @property
    def complete(self) -> bool:
        return bool(self.url and self.anon_key and self.email)


class CloudClient(Protocol):
    def sign_in(self, email: str, password: str) -> AuthSession: ...

    def sign_up(self, email: str, password: str) -> AuthSession | None: ...

    def refresh(self, refresh_token: str) -> AuthSession: ...

    def upsert(self, table: str, rows: Sequence[Mapping[str, Any]], access_token: str) -> None: ...

    def close(self) -> None: ...


ClientFactory = Callable[[str, str], CloudClient]


def check_url(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    if cleaned.startswith(("https://", *LOCAL_HOSTS)):
        return cleaned
    raise CloudSetupError(
        "Use the Project URL from Supabase (Project Settings > API), for example "
        "https://abcdefgh.supabase.co",
    )


def _jwt_role(key: str) -> str | None:
    parts = key.split(".")
    if len(parts) != 3:
        return None
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except ValueError:
        return None
    return str(payload.get("role")) if isinstance(payload, dict) else None


def check_key(key: str) -> str:
    """Accept the anon (publishable) key and refuse the service key (spec D5)."""
    cleaned = key.strip()
    if not cleaned:
        raise CloudSetupError("Paste the anon public key from Supabase (Project Settings > API).")
    if cleaned.startswith("sb_secret_") or _jwt_role(cleaned) == "service_role":
        raise CloudSetupError(
            "This is the secret service key. Never put it in the app: it ignores row-level "
            "security. Paste the anon (publishable) key instead.",
        )
    return cleaned


def load_cloud(profile_dir: Path) -> CloudSettings:
    try:
        raw = json.loads((profile_dir / CLOUD_FILE_NAME).read_text(encoding="utf-8"))
        return CloudSettings.model_validate(raw)
    except (OSError, ValueError):
        return CloudSettings()


def save_cloud(profile_dir: Path, settings: CloudSettings) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    target = profile_dir / CLOUD_FILE_NAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(target)


def token_name(profile: str) -> str:
    return f"{safe_profile_name(profile)}/supabase-refresh-token"


class CloudAccount:
    """The cloud settings and the sign-in of one profile. Network calls block: use a worker."""

    def __init__(
        self,
        profile: str,
        profile_dir: Path,
        engine: SyncEngine,
        credentials: CredentialStore,
        client_factory: ClientFactory,
    ) -> None:
        self.profile = profile
        self.profile_dir = profile_dir
        self.engine = engine
        self._credentials = credentials
        self._factory = client_factory
        self._client: CloudClient | None = None
        self.settings = load_cloud(profile_dir)

    @property
    def signed_in(self) -> bool:
        return self.engine.session is not None

    def restore(self) -> bool:
        """At start-up: continue with the saved sign-in. No network call happens here."""
        settings = self.settings
        if not (settings.enabled and settings.complete):
            return False
        try:
            token = self._credentials.get(token_name(self.profile))
        except CredentialError:
            token = None
        client = self._connect(settings)
        if not token:
            self.engine.configure(client, None, self._remember)
            return False
        MASKER.register(token)
        session = AuthSession("", settings.email, access_token="", refresh_token=token)
        self.engine.configure(client, session, self._remember)
        return True

    def sign_in(self, url: str, anon_key: str, email: str, password: str) -> AuthSession:
        settings = self._checked(url, anon_key, email)
        MASKER.register(password)
        client = self._connect(settings)
        session = client.sign_in(settings.email, password)
        self._activate(settings, client, session)
        return session

    def sign_up(self, url: str, anon_key: str, email: str, password: str) -> AuthSession | None:
        """Create the cloud account. None means: confirm the email, then sign in."""
        settings = self._checked(url, anon_key, email)
        MASKER.register(password)
        client = self._connect(settings)
        session = client.sign_up(settings.email, password)
        if session is not None:
            self._activate(settings, client, session)
        else:
            self.settings = settings
            save_cloud(self.profile_dir, settings)
        return session

    def sign_out(self) -> None:
        self.settings = self.settings.model_copy(update={"enabled": False})
        save_cloud(self.profile_dir, self.settings)
        self._forget_token()
        self.engine.configure(None, None)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _checked(self, url: str, anon_key: str, email: str) -> CloudSettings:
        address = email.strip()
        if "@" not in address:
            raise CloudSetupError("Enter the email address of your cloud account.")
        return CloudSettings(url=check_url(url), anon_key=check_key(anon_key), email=address)

    def _connect(self, settings: CloudSettings) -> CloudClient:
        self.close()
        self._client = self._factory(settings.url, settings.anon_key)
        return self._client

    def _activate(self, settings: CloudSettings, client: CloudClient, session: AuthSession) -> None:
        self.settings = settings.model_copy(update={"enabled": True})
        save_cloud(self.profile_dir, self.settings)
        self._remember(session)
        self.engine.configure(client, session, self._remember)

    def _remember(self, session: AuthSession | None) -> None:
        """Keep the newest refresh token in Credential Manager, or forget it on sign-out."""
        if session is None:
            self._forget_token()
            return
        MASKER.register(session.access_token)
        MASKER.register(session.refresh_token)
        with contextlib.suppress(CredentialError):
            self._credentials.set(token_name(self.profile), session.refresh_token)

    def _forget_token(self) -> None:
        with contextlib.suppress(CredentialError):
            self._credentials.delete(token_name(self.profile))
