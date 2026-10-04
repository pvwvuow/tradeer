"""The Go-Live checklist on the Strategies page (Phase 13b, spec C9, F3).

Shows every check of the selected strategy with its value and threshold, the approval on
this account, and the three actions: confirm the risk settings were reviewed, approve for
Auto (a plain confirmation when every check passed, else only the typed override phrase)
and remove the approval. Every action is in the audit log (through the desk).
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.engine.go_live_desk import GoLiveDesk
from app.engine.go_live_gate import OVERRIDE_PHRASE, GateReport
from app.strategies.registry import STRATEGIES
from app.ui.pages import styled_label

INTRO = (
    "Auto mode sends signals without asking. On a REAL account a strategy may do that only "
    "after this checklist approved its current settings on that account. A failed check can "
    f"only be overridden by typing {OVERRIDE_PHRASE}; every approval is in the audit log."
)
RISK_TEXT = (
    "Confirm that you reviewed the risk settings on the Risk page: risk per trade, daily loss, "
    "drawdown, open risk and open trades. Changing them later needs a new review."
)
Confirm = Callable[[str, str], bool]
TypedAnswer = Callable[[str, str], str]


def _ask(parent: QWidget, title: str, text: str) -> bool:
    answer = QMessageBox.question(parent, title, text)
    return answer == QMessageBox.StandardButton.Yes


def _typed(parent: QWidget, title: str, text: str) -> str:
    answer, ok = QInputDialog.getText(parent, title, text)
    return answer if ok else ""


class GoLivePanel(QFrame):
    def __init__(self, desk: GoLiveDesk, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.desk = desk
        self.setObjectName("GoLivePanel")
        self.setProperty("role", "card")
        self.confirm: Confirm = lambda title, text: _ask(self, title, text)
        self.typed: TypedAnswer = lambda title, text: _typed(self, title, text)
        self.report: GateReport | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        layout.addWidget(styled_label("Go-Live checklist", "brand"))
        layout.addWidget(styled_label(INTRO, "muted", wrap=True))
        row = QHBoxLayout()
        self.strategy = QComboBox()
        self.strategy.setObjectName("GoLiveStrategy")
        for name, kind in STRATEGIES.items():
            self.strategy.addItem(kind.title, name)
        self.strategy.currentIndexChanged.connect(self._strategy_changed)
        self.check_button = QPushButton("Check again")
        self.check_button.clicked.connect(self.refresh)
        row.addWidget(self.strategy)
        row.addWidget(self.check_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.checks = styled_label("", "status", wrap=True)
        self.checks.setObjectName("GoLiveChecks")
        layout.addWidget(self.checks)
        self.approval = styled_label("", "muted", wrap=True)
        self.approval.setObjectName("GoLiveApproval")
        layout.addWidget(self.approval)
        buttons = QHBoxLayout()
        self.review_button = QPushButton("Risk settings reviewed")
        self.review_button.clicked.connect(self.review_risk)
        self.approve_button = QPushButton("Approve for Auto")
        self.approve_button.setObjectName("GoLiveApprove")
        self.approve_button.clicked.connect(self.ask_approve)
        self.revoke_button = QPushButton("Remove approval")
        self.revoke_button.clicked.connect(self.revoke)
        for button in (self.review_button, self.approve_button, self.revoke_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.status = styled_label("", "muted", wrap=True)
        self.status.setObjectName("GoLiveStatus")
        layout.addWidget(self.status)
        self.refresh()

    def selected(self) -> str:
        return str(self.strategy.currentData())

    def _strategy_changed(self, _index: int) -> None:
        self.status.setText("")
        self.refresh()

    def refresh(self) -> GateReport | None:
        name = self.selected()
        try:
            self.report = self.desk.report(name)
            approval = self.desk.approval_text(name)
        except Exception as error:
            self.report = None
            self.checks.setText(f"The checklist could not be read: {type(error).__name__}")
            return None
        self.checks.setText("\n".join([self.report.summary, *self.report.lines]))
        self.approval.setText(approval)
        return self.report

    def review_risk(self) -> bool:
        if not self.confirm("Risk settings reviewed?", RISK_TEXT):
            return False
        self.status.setText(self.desk.review_risk())
        self.refresh()
        return True

    def ask_approve(self) -> bool:
        report = self.refresh()
        if report is None:
            return False
        name = self.selected()
        if report.passed:
            question = (
                f"Every check passed. Allow Auto mode to trade {name} with its current "
                "settings on this account?"
            )
            if not self.confirm("Approve for Auto?", question):
                return False
            ok, message = self.desk.approve(name)
        else:
            failed = ", ".join(report.failed)
            typed = self.typed(
                "Override the Go-Live gate",
                f"These checks failed: {failed}.\nAuto would trade {name} with real money "
                f"anyway.\nType {OVERRIDE_PHRASE} to approve:",
            )
            ok, message = self.desk.approve(name, typed)
        self.status.setText(message)
        self.refresh()
        return ok

    def revoke(self) -> bool:
        name = self.selected()
        question = f"Auto will no longer send {name} signals on a REAL account."
        if not self.confirm("Remove the approval?", question):
            return False
        self.status.setText(self.desk.revoke(name))
        self.refresh()
        return True
