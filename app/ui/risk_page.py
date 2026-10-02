"""The Risk page (spec F3 page 11): limits and their usage, currency exposure, risk events,
the profiles and the stop / re-enable controls.

The limits live in the engine (`RiskManager`); this page only shows them and asks for
changes. Stopping and re-enabling go through the manager's queue and are applied in the
analysis thread. Re-enabling needs the typed word ENABLE. Settings are saved in `risk.json`
and every change is in the audit log.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.observability.logger import audit
from app.risk.report import ENABLE_WORD, exposure_rows, risk_in_words, status_text, usage_rows
from app.risk.risk_manager import RiskManager, RiskSnapshot
from app.risk.settings import (
    PROFILE_NOTES,
    PROFILE_TITLES,
    PROFILES,
    RiskConfig,
    RiskSettings,
    RiskSettingsSource,
    matching_profile,
    profile_config,
)
from app.ui.pages import PAGE_MARGIN, card_frame, styled_label
from app.ui.strategies_page import ParamsForm

INTRO = (
    "These limits are enforced by the engine on every signal and cannot be skipped. Orders "
    "arrive in Phase 8: today the limits decide which signals wait for approval."
)


@dataclass
class RiskContext:
    manager: RiskManager
    settings: RiskSettingsSource


class _Bridge(QObject):
    snapshot = Signal(object)


def _table(columns: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.verticalHeader().setVisible(False)
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _fill(table: QTableWidget, rows: Sequence[tuple[str, ...]]) -> None:
    table.setRowCount(len(rows))
    for row, values in enumerate(rows):
        for column, text in enumerate(values):
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, column, item)


def _local(seconds: float) -> str:
    return datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M")


class RiskPage(QWidget):
    def __init__(self, context: RiskContext | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page_risk")
        self.context = context
        self.bridge = _Bridge()
        self.bridge.snapshot.connect(self.show_snapshot, Qt.ConnectionType.QueuedConnection)
        self.last_snapshot: RiskSnapshot | None = None
        config = context.settings.config if context is not None else RiskConfig()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        layout.setSpacing(16)
        layout.addWidget(styled_label("Risk", "title"))
        layout.addWidget(styled_label(INTRO, "muted", wrap=True))

        state_card, state_layout = card_frame()
        self.state_label = styled_label(status_text(None), "brand", wrap=True)
        self.state_label.setObjectName("RiskState")
        self.words = styled_label("", "muted", wrap=True)
        state_layout.addWidget(self.state_label)
        state_layout.addWidget(self.words)
        buttons = QHBoxLayout()
        self.stop_button = QPushButton("Stop new entries")
        self.stop_button.setObjectName("RiskStop")
        self.stop_button.setProperty("variant", "danger")
        self.stop_button.clicked.connect(self.stop_entries)
        self.enable_button = QPushButton("Re-enable trading…")
        self.enable_button.setObjectName("RiskEnable")
        self.enable_button.clicked.connect(self.ask_enable)
        buttons.addWidget(self.stop_button)
        buttons.addWidget(self.enable_button)
        buttons.addStretch(1)
        state_layout.addLayout(buttons)
        layout.addWidget(state_card)

        self.usage = _table(["Limit", "Used now", "Allowed", "Note"])
        self.usage.setObjectName("RiskUsage")
        layout.addWidget(styled_label("Limit usage", "section"))
        layout.addWidget(self.usage)
        self.exposure = _table(["Currency", "Net risk"])
        self.exposure.setObjectName("RiskExposure")
        layout.addWidget(styled_label("Currency exposure", "section"))
        layout.addWidget(self.exposure)
        self.events = _table(["Time (local)", "Event", "Details"])
        self.events.setObjectName("RiskEvents")
        layout.addWidget(styled_label("Risk events", "section"))
        layout.addWidget(self.events)

        settings_card, settings_layout = card_frame()
        settings_layout.addWidget(styled_label("Profile and limits", "brand"))
        row = QHBoxLayout()
        self.profile = QComboBox()
        self.profile.setObjectName("RiskProfile")
        for name in PROFILES:
            self.profile.addItem(PROFILE_TITLES[name], name)
        self.apply_button = QPushButton("Use this profile")
        self.apply_button.setObjectName("RiskApplyProfile")
        self.apply_button.clicked.connect(self.apply_profile)
        row.addWidget(self.profile)
        row.addWidget(self.apply_button)
        row.addStretch(1)
        settings_layout.addLayout(row)
        self.profile_note = styled_label("", "muted", wrap=True)
        settings_layout.addWidget(self.profile_note)
        self.form = ParamsForm(RiskSettings, config.settings.model_dump(mode="json"))
        settings_layout.addWidget(self.form)
        save_row = QHBoxLayout()
        self.save_button = QPushButton("Save risk settings")
        self.save_button.setObjectName("SaveRisk")
        self.save_button.clicked.connect(self.save)
        save_row.addWidget(self.save_button)
        save_row.addStretch(1)
        settings_layout.addLayout(save_row)
        self.status = styled_label("", "muted", wrap=True)
        settings_layout.addWidget(self.status)
        layout.addWidget(settings_card)
        layout.addStretch(1)

        self._show_profile(config)
        for button in (self.stop_button, self.enable_button, self.apply_button, self.save_button):
            button.setEnabled(context is not None)
        if context is not None:
            context.manager.add_listener(self.bridge.snapshot.emit)
            self.show_snapshot(context.manager.snapshot)

    # Snapshots -------------------------------------------------------------------------
    def show_snapshot(self, snapshot: object) -> None:
        """Slot: a new risk snapshot (from the analysis thread, queued)."""
        if not isinstance(snapshot, RiskSnapshot):
            return
        self.last_snapshot = snapshot
        usage = snapshot.usage
        settings = snapshot.config.settings
        self.state_label.setText(status_text(usage))
        self.enable_button.setEnabled(self.context is not None and bool(snapshot.halted))
        if usage is None:
            self.words.setText(snapshot.message)
            _fill(self.usage, [])
            _fill(self.exposure, [])
        else:
            self.words.setText(risk_in_words(usage, settings))
            _fill(self.usage, usage_rows(usage, settings))
            _fill(self.exposure, exposure_rows(usage))
        events = [(_local(event.time), event.type, event.message) for event in snapshot.events]
        _fill(self.events, events)

    # Actions ---------------------------------------------------------------------------
    def stop_entries(self) -> None:
        if self.context is None:
            return
        self.context.manager.request_stop("stopped on the Risk page")
        audit("risk: new entries stopped", before=None, after={"halted": "manual"})
        self.status.setText("Stopping new entries: applied within a few seconds.")

    def ask_enable(self) -> None:
        text, ok = QInputDialog.getText(
            self,
            "Re-enable trading",
            f"Check why trading stopped. Type {ENABLE_WORD} to allow new entries again:",
        )
        self.enable(text if ok else "")

    def enable(self, typed: str) -> bool:
        """Re-enable only after the exact typed confirmation (spec C6)."""
        if self.context is None or typed.strip() != ENABLE_WORD:
            self.status.setText(f"Not re-enabled: type {ENABLE_WORD} exactly.")
            return False
        self.context.manager.request_enable()
        audit("risk: trading re-enabled", before={"halted": self.last_halted()}, after=None)
        self.status.setText("Re-enabling: the drawdown restarts from the current equity.")
        return True

    def last_halted(self) -> str:
        return self.last_snapshot.halted if self.last_snapshot is not None else ""

    def apply_profile(self) -> None:
        name = str(self.profile.currentData())
        preset = profile_config(name)
        self.form.set_values(preset.settings.model_dump(mode="json"))
        self._show_profile(preset)
        self.status.setText(f"{PROFILE_TITLES[name]} values loaded. Press Save to use them.")

    def save(self) -> bool:
        if self.context is None:
            return False
        settings, problems = self.form.validated()
        if problems or not isinstance(settings, RiskSettings):
            self.status.setText("Not saved. " + " ".join(problems))
            return False
        before = self.context.settings.config
        config = RiskConfig(profile=matching_profile(settings), settings=settings)
        self.context.settings.save(config)
        audit(
            "risk settings changed",
            before=before.model_dump(mode="json"),
            after=config.model_dump(mode="json"),
        )
        self._show_profile(config)
        self.status.setText(f"Saved ({config.profile_title()}). Used for the next signal.")
        return True

    def _show_profile(self, config: RiskConfig) -> None:
        index = self.profile.findData(config.profile)
        if index >= 0:
            self.profile.setCurrentIndex(index)
        title = config.profile_title()
        self.profile_note.setText(f"Current: {title}. {PROFILE_NOTES.get(config.profile, '')}")
