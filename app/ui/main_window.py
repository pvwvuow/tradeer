"""Phase 1 main window: simple/advanced shell with empty, honest pages."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QListWidget,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.domain.config import TradingDefaults
from app.ui.theme import DARK_QSS, LIGHT_QSS


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.defaults = TradingDefaults()
        self.setWindowTitle("MT5 Trading Workstation · Phase 1")
        self.resize(1180, 760)
        self.setStyleSheet(DARK_QSS)
        root = QWidget()
        layout = QVBoxLayout(root)
        header = QFrame(objectName="card")
        header_layout = QVBoxLayout(header)
        header_layout.addWidget(QLabel("MT5 Trading Workstation", objectName="title"))
        header_layout.addWidget(
            QLabel("Foundation shell · no account connected · no orders", objectName="muted")
        )
        layout.addWidget(header)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        self.pages = QStackedWidget()
        self.navigation = QListWidget()
        page_names = [
            "Home",
            "Market",
            "Signals",
            "Positions & Trades",
            "Analytics",
            "Risk",
            "Settings",
        ]
        self.navigation.addItems(page_names)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        body_layout.addWidget(self.navigation)
        body_layout.addWidget(self.pages)
        layout.addWidget(body, 1)
        for name in page_names:
            self.pages.addWidget(self._page(name))
        layout.addWidget(
            QLabel(self.defaults.safety_summary() + " · status: stopped", objectName="muted")
        )
        self.setCentralWidget(root)
        self.navigation.setCurrentRow(0)

    def _page(self, name: str) -> QWidget:
        page = QFrame(objectName="card")
        page_layout = QVBoxLayout(page)
        page_layout.addWidget(QLabel(name, objectName="title"))
        if name == "Home":
            page_layout.addWidget(
                QLabel(
                    "Paper mode is the default. Connect MT5 in a later phase; "
                    "this foundation never trades.",
                    objectName="muted",
                )
            )
            theme_button = QPushButton("Switch to light theme")
            theme_button.setObjectName("accent")
            theme_button.clicked.connect(self._toggle_theme)
            page_layout.addWidget(theme_button, alignment=Qt.AlignmentFlag.AlignLeft)
        else:
            page_layout.addWidget(
                QLabel(
                    "Not implemented in Phase 1. The page shell is ready for its phase.",
                    objectName="muted",
                )
            )
        page_layout.addStretch()
        return page

    def _toggle_theme(self) -> None:
        self.setStyleSheet(LIGHT_QSS if self.styleSheet() == DARK_QSS else DARK_QSS)
