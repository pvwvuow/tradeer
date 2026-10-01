"""Ctrl+K command palette (Advanced view only, spec F2)."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui.commands import Command, filter_commands


class CommandPalette(QDialog):
    def __init__(self, commands: Sequence[Command], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._commands = list(commands)
        self._visible: list[Command] = []
        self.setObjectName("CommandPalette")
        self.setWindowTitle("Command palette")
        self.setModal(True)
        self.resize(560, 380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Type a page or command name")
        self.results = QListWidget()
        layout.addWidget(self.search)
        layout.addWidget(self.results)
        self.search.textChanged.connect(self._refresh)
        self.search.returnPressed.connect(self.run_selected)
        self.results.itemActivated.connect(self._run_item)
        self._refresh("")

    def visible_titles(self) -> list[str]:
        return [command.title for command in self._visible]

    def run_selected(self) -> None:
        row = self.results.currentRow()
        if 0 <= row < len(self._visible):
            command = self._visible[row]
            self.accept()
            command.action()

    def _refresh(self, query: str) -> None:
        self._visible = filter_commands(self._commands, query)
        self.results.clear()
        for command in self._visible:
            item = QListWidgetItem(f"{command.title}    {command.hint}")
            item.setData(Qt.ItemDataRole.UserRole, command.command_id)
            self.results.addItem(item)
        if self._visible:
            self.results.setCurrentRow(0)

    def _run_item(self, item: QListWidgetItem) -> None:
        self.results.setCurrentItem(item)
        self.run_selected()
