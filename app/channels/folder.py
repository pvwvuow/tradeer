"""The Telegram folder the app reads (docs/SIGNAL_DESK.md 3.2, phase 21c). Pure.

The user's chat folder (a dialog filter) named "AI Lab" holds the sources: every channel
and group in it. Private chats and bots in the folder are ignored. Adding or removing a
channel in Telegram is how the list is managed; the app never joins or leaves anything.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

TEXT_LIMIT = 4096  # one Telegram message holds at most 4096 characters
PREVIEW_CHARACTERS = 120


class PeerKind(StrEnum):
    CHANNEL = "channel"
    GROUP = "group"
    USER = "user"
    BOT = "bot"


@dataclass(frozen=True)
class Folder:
    """One chat folder: its title and the ids of the chats it includes."""

    title: str
    peers: tuple[int, ...]


@dataclass(frozen=True)
class Peer:
    id: int
    title: str
    kind: PeerKind
    username: str = ""

    @property
    def source(self) -> bool:
        """Channels and groups are sources; people and bots are not."""
        return self.kind in (PeerKind.CHANNEL, PeerKind.GROUP)


@dataclass(frozen=True)
class ChannelMessage:
    """A message as the store keeps it: the text is data, never an instruction."""

    channel_id: int
    message_id: int
    date: float  # UTC seconds
    text: str
    reply_to: int | None = None


def same_name(left: str, right: str) -> bool:
    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()


def find_folder(folders: Iterable[Folder], name: str) -> Folder | None:
    """The folder with this name (case and spaces do not matter), or None."""
    for folder in folders:
        if same_name(folder.title, name):
            return folder
    return None


def folder_sources(folder: Folder, peers: Sequence[Peer]) -> list[Peer]:
    """The channels and groups of the folder, in the folder's order."""
    known = {peer.id: peer for peer in peers}
    found: list[Peer] = []
    for peer_id in folder.peers:
        peer = known.get(peer_id)
        if peer is not None and peer.source and peer not in found:
            found.append(peer)
    return found


def clean_text(text: str) -> str:
    """The message text as stored: no NUL characters, at most one message long."""
    return text.replace("\x00", "")[:TEXT_LIMIT]


def preview(text: str) -> str:
    """One short line of a message for the channel list."""
    flat = " ".join(text.split())
    if len(flat) <= PREVIEW_CHARACTERS:
        return flat
    return flat[: PREVIEW_CHARACTERS - 1].rstrip() + "\u2026"
