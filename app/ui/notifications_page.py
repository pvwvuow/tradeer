"""Settings > Notifications (spec C14): which events show a Windows notification and which go
to Telegram, quiet hours, and the Telegram bot (token in Windows Credential Manager, the
allowed chat ids and the PIN for commands). Every save is in the audit log."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.core.credentials import CredentialError, CredentialStore, read_password, save_password
from app.notify.center import NotificationCenter
from app.notify.events import EventKind
from app.notify.settings import NotificationSettings, NotificationSettingsSource
from app.observability.logger import audit
from app.ui.pages import PAGE_MARGIN, styled_label

INTRO = (
    "Urgent alerts (a limit hit, MT5 disconnected, an error) are always shown, also during "
    "quiet hours. Telegram is optional: create a bot with @BotFather, paste its token here, "
    "send /start to it and add your chat id. Commands need the PIN first."
)


def _no_bot() -> str:
    return "Telegram is not available in this run."


@dataclass
class NotificationsContext:
    source: NotificationSettingsSource
    credentials: CredentialStore
    token_name: str
    center: NotificationCenter
    apply_bot: Callable[[], str] = _no_bot  # start or stop the bot to match the settings
    test: Callable[[], str] = _no_bot  # send a test notice through every channel


def parse_chat_ids(text: str) -> list[int]:
    found: list[int] = []
    for part in text.replace(";", ",").replace(" ", ",").split(","):
        value = part.strip()
        if not value:
            continue
        if not value.lstrip("-").isdigit():
            raise ValueError(f"not a chat id: {value}")
        number = int(value)
        if number not in found:
            found.append(number)
    return found


class NotificationsPage(QWidget):
    def __init__(self, context: NotificationsContext | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page_notifications")
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
        layout.addWidget(styled_label("Notifications", "title"))
        layout.addWidget(styled_label(INTRO, "muted", wrap=True))
        grid = QGridLayout()
        grid.addWidget(styled_label("Event", "muted"), 0, 0)
        grid.addWidget(styled_label("Windows", "muted"), 0, 1)
        grid.addWidget(styled_label("Telegram", "muted"), 0, 2)
        self.toast_boxes: dict[EventKind, QCheckBox] = {}
        self.telegram_boxes: dict[EventKind, QCheckBox] = {}
        kinds: list[EventKind] = list(EventKind)
        for row, kind in enumerate(kinds, start=1):
            title = kind.label + (" (urgent)" if kind.urgent else "")
            grid.addWidget(styled_label(title, "body"), row, 0)
            toast = QCheckBox()
            telegram = QCheckBox()
            self.toast_boxes[kind] = toast
            self.telegram_boxes[kind] = telegram
            grid.addWidget(toast, row, 1)
            grid.addWidget(telegram, row, 2)
        layout.addLayout(grid)
        quiet = QHBoxLayout()
        self.quiet_box = QCheckBox("Quiet hours from")
        self.quiet_start = QLineEdit()
        self.quiet_end = QLineEdit()
        for edit in (self.quiet_start, self.quiet_end):
            edit.setMaximumWidth(80)
            edit.setPlaceholderText("HH:MM")
        quiet.addWidget(self.quiet_box)
        quiet.addWidget(self.quiet_start)
        quiet.addWidget(styled_label("to", "muted"))
        quiet.addWidget(self.quiet_end)
        quiet.addWidget(styled_label("(this PC's time)", "muted"))
        quiet.addStretch(1)
        layout.addLayout(quiet)
        layout.addWidget(styled_label("Telegram", "heading"))
        self.telegram_box = QCheckBox("Use the Telegram bot")
        self.reports_box = QCheckBox("Send the daily and weekly reports")
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setObjectName("TelegramToken")
        self.chats = QLineEdit()
        self.chats.setObjectName("TelegramChats")
        self.chats.setPlaceholderText("Allowed chat ids, separated by commas")
        self.pin = QLineEdit()
        self.pin.setEchoMode(QLineEdit.EchoMode.Password)
        self.pin.setObjectName("TelegramPin")
        for widget in (self.telegram_box, self.reports_box, self.token, self.chats, self.pin):
            layout.addWidget(widget)
        buttons = QHBoxLayout()
        self.save_button = QPushButton("Save")
        self.save_button.setProperty("variant", "accent")
        self.save_button.clicked.connect(self.save)
        self.test_button = QPushButton("Send a test notification")
        self.test_button.clicked.connect(self.send_test)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.test_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.status = styled_label("", "muted", wrap=True)
        self.status.setObjectName("NotificationsStatus")
        layout.addWidget(self.status)
        layout.addWidget(styled_label("Recent notifications", "heading"))
        self.history = QListWidget()
        self.history.setObjectName("NotificationsHistory")
        self.history.setMinimumHeight(160)
        layout.addWidget(self.history)
        layout.addStretch(1)
        area.setWidget(body)
        outer.addWidget(area)
        if context is None:
            self.status.setText("Notifications are not running.")
            self.save_button.setEnabled(False)
            self.test_button.setEnabled(False)
        else:
            self.show_settings(context.source.settings)
            self.show_history()

    def show_settings(self, settings: NotificationSettings) -> None:
        for kind in EventKind:
            self.toast_boxes[kind].setChecked(settings.wants("toast", kind))
            self.telegram_boxes[kind].setChecked(settings.wants("telegram", kind))
        self.quiet_box.setChecked(settings.quiet_hours)
        self.quiet_start.setText(settings.quiet_start)
        self.quiet_end.setText(settings.quiet_end)
        self.telegram_box.setChecked(settings.telegram_enabled)
        self.reports_box.setChecked(settings.send_reports)
        self.chats.setText(", ".join(str(chat) for chat in settings.chat_ids))
        self.pin.clear()
        saved_pin = "PIN saved (type a new one to change it)"
        self.pin.setPlaceholderText(saved_pin if settings.pin_hash else "PIN, 4 to 12 digits")
        self.token.clear()
        saved = False
        if self.context is not None:
            try:
                saved = bool(read_password(self.context.credentials, self.context.token_name))
            except CredentialError:
                saved = False
        self.token.setPlaceholderText(
            "Bot token saved in Credential Manager (paste a new one to change it)"
            if saved
            else "Bot token from @BotFather",
        )

    def collect(self, old: NotificationSettings) -> NotificationSettings:
        """The settings on screen (raises ValueError with a readable message)."""
        settings = NotificationSettings.model_validate(
            {
                **old.model_dump(),
                "toast": {k.value: box.isChecked() for k, box in self.toast_boxes.items()},
                "telegram": {k.value: box.isChecked() for k, box in self.telegram_boxes.items()},
                "quiet_hours": self.quiet_box.isChecked(),
                "quiet_start": self.quiet_start.text().strip() or "22:00",
                "quiet_end": self.quiet_end.text().strip() or "07:00",
                "telegram_enabled": self.telegram_box.isChecked(),
                "send_reports": self.reports_box.isChecked(),
                "chat_ids": parse_chat_ids(self.chats.text()),
            },
        )
        pin = self.pin.text().strip()
        if pin:
            settings = settings.with_pin(pin)
        if settings.telegram_enabled and not settings.chat_ids:
            raise ValueError("add at least one allowed chat id to use Telegram")
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
        token = self.token.text().strip()
        try:
            if token:
                save_password(context.credentials, context.token_name, token)
        except CredentialError as error:
            self.status.setText(f"The token could not be saved: {error}")
            return False
        context.source.save(settings)
        before = f"telegram {'on' if old.telegram_enabled else 'off'}, {len(old.chat_ids)} chat(s)"
        after = (
            f"telegram {'on' if settings.telegram_enabled else 'off'}, "
            f"{len(settings.chat_ids)} chat(s), quiet hours "
            f"{'on' if settings.quiet_hours else 'off'}"
            + (", new token" if token else "")
            + (", new PIN" if self.pin.text().strip() else "")
        )
        audit("notification settings saved", before=before, after=after)
        bot = context.apply_bot()
        self.show_settings(settings)
        self.status.setText(f"Saved. {bot}")
        return True

    def send_test(self) -> str:
        context = self.context
        if context is None:
            return ""
        result = context.test()
        self.status.setText(result)
        self.show_history()
        return result

    def show_history(self) -> None:
        if self.context is None:
            return
        self.history.clear()
        for delivered in reversed(self.context.center.history[-50:]):
            where = ", ".join(delivered.channels) or ("held" if delivered.held else "not sent")
            notice = delivered.notice
            self.history.addItem(f"{notice.title}: {notice.text[:120]} [{where}]")
