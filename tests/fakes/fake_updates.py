"""A scripted update backend for the update service and page tests (never shipped)."""

from collections.abc import Callable

from app.updates.backend import UpdateError, UpdateOffer


class FakeBackend:
    def __init__(self, current: str = "0.12.0", reason: str = "") -> None:
        self.current = current
        self.reason = reason
        self.offer: UpdateOffer | None = None
        self.versions: dict[str, UpdateOffer] = {}
        self.fail_check = False
        self.fail_download = False
        self.downloaded: list[str] = []
        self.applied: list[str] = []

    def unavailable_reason(self) -> str:
        return self.reason

    def current_version(self) -> str:
        return self.current

    def check(self) -> UpdateOffer | None:
        if self.fail_check:
            raise UpdateError("no internet")
        return self.offer

    def check_version(self, version: str) -> UpdateOffer | None:
        return self.versions.get(version)

    def download(self, offer: UpdateOffer, progress: Callable[[int], None]) -> None:
        if self.fail_download:
            raise UpdateError("checksum mismatch")
        for value in (10, 50, 100):
            progress(value)
        self.downloaded.append(offer.version)

    def apply_after_exit(self, offer: UpdateOffer) -> None:
        self.applied.append(offer.version)
