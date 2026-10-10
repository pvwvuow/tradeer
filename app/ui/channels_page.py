"""Settings > Telegram channels (docs/SIGNAL_DESK.md 3.1 and 3.2, phase 21c3).

Connect your own Telegram account (api_id and api_hash from my.telegram.org), choose the
proxy (the system proxy by default, needed in Iran), log in once with the phone number, the
code Telegram sends and the two-step password, and turn each channel of the "AI Lab" folder
on or off. The api_hash, the session and the proxy secret go to Windows Credential Manager;
`channels.json` holds the rest. Reading only: signal cards from channels come in phase 21d.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.channels.folder import preview
from app.channels.reader import ChannelReader, LoginError, ReaderState, ReaderStatus
from app.channels.secrets import CredentialSecrets
from app.channels.settings import ChannelSettings, ChannelSettingsSource, ProxyKind
from app.core.credentials import CredentialError
from app.observability.logger import audit
from app.storage.channel_store import ChannelRepository
from app.ui.pages import PAGE_MARGIN, styled_label

INTRO = (
    "The app reads your own Telegram account, but only the folder named below. It never "
    "posts, reacts, joins, leaves or marks anything as read. For now the messages of the "
    "channels you turn on are only stored; signal cards from channels come in a later "
    "version, and every order will still need your hold-to-confirm."
)
STEPS = (
    "1. Open my.telegram.org and log in with your phone number.\n"
    "2. Open API development tools and create an app (any name and short name).\n"
    "3. Copy api_id and api_hash here and Save. Then log in below with your phone number."
)
FOLDER_HELP = (
    'In Telegram, make a chat folder named "AI Lab" (Settings > Folders) and add the signal '
    "channels to it. New channels start off: turn them on in the list."
)
MISSING_TEXT = (
    "Telegram support (Telethon) is not in this copy of the app. Running from source: "
    "pip install telethon==1.44.0, then start the app again."
)
PROXY_LABELS: dict[ProxyKind, str] = {
    ProxyKind.SYSTEM: "System proxy (Windows settings, e.g. your VPN)",
    ProxyKind.NONE: "No proxy",
    ProxyKind.SOCKS5: "SOCKS5 proxy",
    ProxyKind.HTTP: "HTTP proxy",
    ProxyKind.MTPROTO: "MTProto proxy (the secret goes in the password field)",
}
CHANNEL_ROLE = Qt.ItemDataRole.UserRole


@dataclass
class ChannelsContext:
    source: ChannelSettingsSource
    secrets: CredentialSecrets
    reader: ChannelReader
    repository: ChannelRepository
    now: Callable[[], float] = field(default=time.time)

    def restart(self) -> ReaderState:
        """Stop the reader and start it again with the saved settings."""
        self.reader.stop()
        return self.reader.start()


class ReaderBridge(QObject):
    """Reader states arrive from the reader thread; the page shows them in the UI thread."""

    changed = Signal(object)


class ChannelsPage(QWidget):
    def __init__(self, context: ChannelsContext | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page_channels")
        self.context = context
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        layout.setSpacing(12)
        layout.addWidget(styled_label("Telegram channels", "title"))
        layout.addWidget(styled_label(INTRO, "muted", wrap=True))
        layout.addWidget(styled_label("Connect", "heading"))
        layout.addWidget(styled_label(STEPS, "body", wrap=True))
        self.enabled_box = QCheckBox("Read my Telegram folder")
        self.enabled_box.setObjectName("ChannelsEnabled")
        layout.addWidget(self.enabled_box)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        self.api_id = self._edit("ChannelsApiId", "api_id (a number)")
        self.api_hash = self._edit("ChannelsApiHash", "api_hash", secret=True)
        self.folder = self._edit("ChannelsFolder", "AI Lab")
        self.proxy = QComboBox()
        self.proxy.setObjectName("ChannelsProxy")
        self.proxy.setAccessibleName("Proxy")
        for kind, label in PROXY_LABELS.items():
            self.proxy.addItem(label, kind.value)
        self.proxy.currentIndexChanged.connect(self._proxy_changed)
        self.proxy_host = self._edit("ChannelsProxyHost", "Proxy host, e.g. 127.0.0.1")
        self.proxy_port = self._edit("ChannelsProxyPort", "Port, e.g. 10808")
        self.proxy_user = self._edit("ChannelsProxyUser", "User name (if any)")
        self.proxy_secret = self._edit("ChannelsProxySecret", "Password or secret", secret=True)
        rows: list[tuple[str, QWidget]] = [
            ("api_id", self.api_id),
            ("api_hash", self.api_hash),
            ("Folder", self.folder),
            ("Proxy", self.proxy),
            ("Proxy host", self.proxy_host),
            ("Proxy port", self.proxy_port),
            ("Proxy user", self.proxy_user),
            ("Proxy password", self.proxy_secret),
        ]
        for row, (title, widget) in enumerate(rows):
            grid.addWidget(styled_label(title, "muted"), row, 0)
            grid.addWidget(widget, row, 1)
        layout.addLayout(grid)
        layout.addWidget(styled_label(FOLDER_HELP, "muted", wrap=True))
        buttons = QHBoxLayout()
        self.save_button = self._button("Save and connect", "ChannelsSave", self.save)
        self.save_button.setProperty("variant", "accent")
        buttons.addWidget(self.save_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.status = styled_label("", "muted", wrap=True)
        self.status.setObjectName("ChannelsStatus")
        layout.addWidget(self.status)
        layout.addWidget(styled_label("Log in", "heading"))
        login = QGridLayout()
        login.setHorizontalSpacing(10)
        self.phone = self._edit("ChannelsPhone", "Phone number with country code, +98...")
        self.code = self._edit("ChannelsCode", "The code Telegram sent you")
        self.password = self._edit("ChannelsPassword", "Two-step password", secret=True)
        self.code_button = self._button("Send code", "ChannelsSendCode", self.send_code)
        self.sign_in_button = self._button("Log in", "ChannelsSignIn", self.sign_in)
        self.password_button = self._button(
            "Send password",
            "ChannelsSendPassword",
            self.send_password,
        )
        steps = [
            (self.phone, self.code_button),
            (self.code, self.sign_in_button),
            (self.password, self.password_button),
        ]
        for row, (edit, button) in enumerate(steps):
            login.addWidget(edit, row, 0)
            login.addWidget(button, row, 1)
        layout.addLayout(login)
        self.logout_button = self._button("Log out", "ChannelsLogOut", self.log_out)
        layout.addWidget(self.logout_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(styled_label("Channels in the folder", "heading"))
        self.channels = QListWidget()
        self.channels.setObjectName("ChannelsList")
        self.channels.setAccessibleName("Channels in the folder")
        self.channels.setMinimumHeight(180)
        self.channels.itemChanged.connect(self._item_changed)
        layout.addWidget(self.channels)
        self.refresh_button = self._button("Read the folder again", "ChannelsRefresh", self.refresh)
        layout.addWidget(self.refresh_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        area.setWidget(body)
        outer.addWidget(area)
        self.bridge = ReaderBridge(self)
        self.bridge.changed.connect(self.show_state)
        if context is None:
            self.status.setText("Telegram channels are not available in this run.")
            for widget in (self.save_button, self.logout_button, self.refresh_button):
                widget.setEnabled(False)
            self._login_step(None)
            return
        self.show_settings(context.source.settings)
        context.reader.add_listener(self.bridge.changed.emit)
        self.show_state(context.reader.state)

    # Building -----------------------------------------------------------------------------
    def _edit(self, name: str, hint: str, secret: bool = False) -> QLineEdit:
        edit = QLineEdit()
        edit.setObjectName(name)
        edit.setPlaceholderText(hint)
        edit.setAccessibleName(hint)
        if secret:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
        return edit

    def _button(self, text: str, name: str, slot: Callable[[], object]) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(name)
        button.clicked.connect(lambda: slot())
        return button

    def _proxy_changed(self) -> None:
        manual = self.proxy_kind() in (ProxyKind.SOCKS5, ProxyKind.HTTP, ProxyKind.MTPROTO)
        for widget in (self.proxy_host, self.proxy_port, self.proxy_user, self.proxy_secret):
            widget.setEnabled(manual)

    def proxy_kind(self) -> ProxyKind:
        return ProxyKind(str(self.proxy.currentData() or ProxyKind.SYSTEM.value))

    # Settings -----------------------------------------------------------------------------
    def show_settings(self, settings: ChannelSettings) -> None:
        self.enabled_box.setChecked(settings.enabled)
        self.api_id.setText(str(settings.api_id) if settings.api_id else "")
        self.folder.setText(settings.folder)
        index = self.proxy.findData(settings.proxy.value)
        self.proxy.setCurrentIndex(max(0, index))
        self.proxy_host.setText(settings.proxy_host)
        self.proxy_port.setText(str(settings.proxy_port) if settings.proxy_port else "")
        self.proxy_user.setText(settings.proxy_user)
        self._proxy_changed()
        self.api_hash.clear()
        self.proxy_secret.clear()
        secrets = self.context.secrets if self.context is not None else None
        saved_hash = bool(secrets.api_hash()) if secrets is not None else False
        saved_secret = bool(secrets.proxy_secret()) if secrets is not None else False
        self.api_hash.setPlaceholderText(
            "api_hash saved in Credential Manager (paste a new one to change it)"
            if saved_hash
            else "api_hash",
        )
        self.proxy_secret.setPlaceholderText(
            "Saved (type a new one to change it)" if saved_secret else "Password or secret",
        )

    def collect(self, old: ChannelSettings) -> ChannelSettings:
        """The settings on screen (raises ValueError with a readable message)."""
        api_id = self.api_id.text().strip()
        port = self.proxy_port.text().strip()
        if api_id and not api_id.isdigit():
            raise ValueError("api_id is a number from my.telegram.org")
        if port and not port.isdigit():
            raise ValueError("the proxy port is a number")
        settings = ChannelSettings.model_validate(
            {
                **old.model_dump(),
                "enabled": self.enabled_box.isChecked(),
                "api_id": int(api_id or 0),
                "folder": self.folder.text(),
                "proxy": self.proxy_kind().value,
                "proxy_host": self.proxy_host.text(),
                "proxy_port": int(port or 0),
                "proxy_user": self.proxy_user.text(),
            },
        )
        if settings.enabled:
            problem = settings.problem()
            if problem:
                raise ValueError(problem[:1].lower() + problem[1:].rstrip("."))
        return settings

    def save(self) -> bool:
        context = self.context
        if context is None:
            return False
        old = context.source.settings
        try:
            settings = self.collect(old)
        except ValueError as error:
            self.status.setText(f"Not saved: {error}")
            return False
        api_hash = self.api_hash.text().strip()
        if settings.enabled and not (api_hash or context.secrets.api_hash()):
            self.status.setText("Not saved: enter the api_hash from my.telegram.org.")
            return False
        secret = self.proxy_secret.text()
        try:
            if api_hash:
                context.secrets.save_api_hash(api_hash)
            if secret:
                context.secrets.save_proxy_secret(secret)
        except CredentialError as error:
            self.status.setText(f"Not saved: Credential Manager failed ({error}).")
            return False
        context.source.save(settings)
        audit(
            "telegram channel settings saved",
            before=f"{'on' if old.enabled else 'off'}, proxy {old.proxy.value}",
            after=(
                f"{'on' if settings.enabled else 'off'}, proxy {settings.proxy.value}"
                + (", new api_hash" if api_hash else "")
                + (", new proxy secret" if secret else "")
            ),
        )
        self.show_settings(settings)
        self.show_state(context.restart())
        return True

    # Reader state -------------------------------------------------------------------------
    def show_state(self, state: object) -> None:
        if not isinstance(state, ReaderState):
            return
        text = MISSING_TEXT if state.status is ReaderStatus.MISSING else state.message
        if state.stored:
            text += f" {state.stored} new message(s) stored since the start."
        self.status.setText(text)
        self._login_step(state.status)
        running = state.status is ReaderStatus.RUNNING
        self.refresh_button.setEnabled(running)
        self.logout_button.setEnabled(running or state.status is ReaderStatus.CONNECTING)
        self.show_channels()

    def _login_step(self, status: ReaderStatus | None) -> None:
        for edit, button, step in (
            (self.phone, self.code_button, ReaderStatus.PHONE),
            (self.code, self.sign_in_button, ReaderStatus.CODE),
            (self.password, self.password_button, ReaderStatus.PASSWORD),
        ):
            edit.setEnabled(status is step)
            button.setEnabled(status is step)

    def show_channels(self) -> None:
        context = self.context
        if context is None:
            return
        counts = context.repository.counts()
        self.channels.blockSignals(True)
        try:
            self.channels.clear()
            for channel in context.repository.channels():
                if not channel.in_folder:
                    continue
                stored = counts.get(channel.channel_id, 0)
                text = f"{channel.title}  ({channel.kind}, {stored} message(s) stored)"
                last = context.repository.messages(channel.channel_id, 1)
                if last:
                    text += f"\n{preview(last[0].text)}"
                item = QListWidgetItem(text)
                item.setData(CHANNEL_ROLE, channel.channel_id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                checked = Qt.CheckState.Checked if channel.enabled else Qt.CheckState.Unchecked
                item.setCheckState(checked)
                self.channels.addItem(item)
        finally:
            self.channels.blockSignals(False)

    def _item_changed(self, item: QListWidgetItem) -> None:
        context = self.context
        if context is None:
            return
        channel_id = int(item.data(CHANNEL_ROLE))
        enabled = item.checkState() == Qt.CheckState.Checked
        context.repository.set_enabled(channel_id, enabled, context.now())
        title = item.text().split("  (", 1)[0]
        audit("telegram channel switched", before=title, after="on" if enabled else "off")
        if context.reader.state.status is ReaderStatus.RUNNING:
            self.refresh()

    # Actions ------------------------------------------------------------------------------
    def _run(self, action: Callable[[], ReaderState]) -> ReaderState | None:
        try:
            state = action()
        except LoginError as error:
            self.status.setText(str(error))
            return None
        self.show_state(state)
        return state

    def send_code(self) -> ReaderState | None:
        if self.context is None:
            return None
        reader, phone = self.context.reader, self.phone.text()
        return self._run(lambda: reader.send_code(phone))

    def sign_in(self) -> ReaderState | None:
        if self.context is None:
            return None
        reader, code = self.context.reader, self.code.text()
        state = self._run(lambda: reader.sign_in(code))
        self.code.clear()
        return state

    def send_password(self) -> ReaderState | None:
        if self.context is None:
            return None
        reader, password = self.context.reader, self.password.text()
        state = self._run(lambda: reader.sign_in_password(password))
        self.password.clear()
        return state

    def log_out(self) -> ReaderState | None:
        if self.context is None:
            return None
        audit("telegram logged out", before="logged in", after="logged out")
        return self._run(self.context.reader.log_out)

    def refresh(self) -> ReaderState | None:
        if self.context is None:
            return None
        return self._run(self.context.reader.refresh)
