"""Main window: Simple/Advanced views, grouped sidebar, status bar and command palette."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from app.__version__ import __version__
from app.core.ui_prefs import ThemeName, UiPrefs, ViewMode, save_prefs
from app.domain.config import TradingDefaults
from app.ui.command_palette import CommandPalette
from app.ui.commands import Command
from app.ui.navigation import ADVANCED_GROUPS, ADVANCED_PAGES, SIMPLE_HOME, pages_in_group
from app.ui.pages import PlaceholderPage, SimpleHomePage, styled_label
from app.ui.theme import build_qss, tokens_for

SIDEBAR_WIDTH = 232


class MainWindow(QMainWindow):
    def __init__(self, prefs: UiPrefs, prefs_dir: Path | None = None) -> None:
        super().__init__()
        self.prefs = prefs
        self.defaults = TradingDefaults()
        self._prefs_dir = prefs_dir
        self._page_index: dict[str, int] = {}
        self._nav_buttons: dict[str, QPushButton] = {}
        self._palette: CommandPalette | None = None
        self.setWindowTitle(f"MT5 Trading Workstation {__version__}")
        self.resize(1280, 800)
        self.setMinimumSize(960, 600)
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_top_bar())
        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        self.sidebar = self._build_sidebar()
        self.pages = QStackedWidget()
        body_layout.addWidget(self.sidebar)
        body_layout.addWidget(self.pages, 1)
        root_layout.addWidget(body, 1)
        self.setCentralWidget(root)
        self.home = SimpleHomePage()
        self._add_page(SIMPLE_HOME.page_id, self.home)
        for spec in ADVANCED_PAGES:
            self._add_page(spec.page_id, PlaceholderPage(spec))
        self._build_status_bar()
        self._shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self._shortcut.activated.connect(self.open_command_palette)
        self.apply_theme(prefs.theme, persist=False)
        self.set_view_mode(prefs.view_mode, persist=False)

    @property
    def nav_buttons(self) -> dict[str, QPushButton]:
        return dict(self._nav_buttons)

    def current_page_id(self) -> str:
        current = self.pages.currentIndex()
        return next((key for key, index in self._page_index.items() if index == current), "")

    def show_page(self, page_id: str) -> None:
        self.pages.setCurrentIndex(self._page_index[page_id])
        button = self._nav_buttons.get(page_id)
        if button is not None:
            button.setChecked(True)

    def set_view_mode(self, mode: ViewMode, persist: bool = True) -> None:
        self.prefs = self.prefs.model_copy(update={"view_mode": mode})
        advanced = mode is ViewMode.ADVANCED
        self.sidebar.setVisible(advanced)
        self._shortcut.setEnabled(advanced)
        self.view_button.setText("Switch to Simple" if advanced else "Switch to Advanced")
        self.show_page("dashboard" if advanced else SIMPLE_HOME.page_id)
        if persist:
            self._persist()

    def toggle_view_mode(self) -> None:
        simple = self.prefs.view_mode is ViewMode.SIMPLE
        self.set_view_mode(ViewMode.ADVANCED if simple else ViewMode.SIMPLE)

    def apply_theme(self, theme: ThemeName, persist: bool = True) -> None:
        self.prefs = self.prefs.model_copy(update={"theme": theme})
        self.setStyleSheet(build_qss(tokens_for(theme)))
        next_theme = "light" if theme is ThemeName.DARK else "dark"
        self.theme_button.setText(f"Switch to {next_theme} theme")
        if persist:
            self._persist()

    def toggle_theme(self) -> None:
        dark = self.prefs.theme is ThemeName.DARK
        self.apply_theme(ThemeName.LIGHT if dark else ThemeName.DARK)

    def commands(self) -> list[Command]:
        items: list[Command] = []
        for spec in ADVANCED_PAGES:
            slot = self._nav_slot(spec.page_id)
            items.append(Command(f"go:{spec.page_id}", f"Go to {spec.title}", spec.group, slot))
        items.append(Command("theme", "Toggle theme", "Appearance", self.toggle_theme))
        items.append(Command("view", "Switch to Simple view", "Appearance", self._to_simple))
        return items

    def open_command_palette(self) -> CommandPalette | None:
        if self.prefs.view_mode is not ViewMode.ADVANCED:
            return None
        self._palette = CommandPalette(self.commands(), self)
        self._palette.open()
        return self._palette

    def _build_top_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("TopBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(8)
        layout.addWidget(styled_label("MT5 Trading Workstation", "brand"))
        layout.addStretch(1)
        self.view_button = QPushButton()
        self.view_button.setObjectName("ViewModeButton")
        self.view_button.clicked.connect(self.toggle_view_mode)
        self.theme_button = QPushButton()
        self.theme_button.setObjectName("ThemeButton")
        self.theme_button.clicked.connect(self.toggle_theme)
        layout.addWidget(self.view_button)
        layout.addWidget(self.theme_button)
        return bar

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(2)
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        for group in ADVANCED_GROUPS:
            layout.addWidget(styled_label(group.upper(), "section"))
            for spec in pages_in_group(group):
                button = QPushButton(spec.title)
                button.setObjectName(f"nav_{spec.page_id}")
                button.setProperty("nav", True)
                button.setCheckable(True)
                button.clicked.connect(self._nav_slot(spec.page_id))
                self._nav_group.addButton(button)
                self._nav_buttons[spec.page_id] = button
                layout.addWidget(button)
        layout.addStretch(1)
        return sidebar

    def _build_status_bar(self) -> None:
        bar = QStatusBar()
        bar.setSizeGripEnabled(False)
        self.connection_label = styled_label("● MT5: not connected", "status")
        self.mode_badge = styled_label(self.defaults.mode.label.upper(), "badge")
        self.bot_state_label = styled_label("Bot: stopped", "status")
        self.kill_switch = QPushButton("Stop trading")
        self.kill_switch.setProperty("variant", "danger")
        self.kill_switch.setEnabled(False)
        self.kill_switch.setToolTip("Nothing is running yet. The kill switch arrives in Phase 8.")
        bar.addWidget(self.connection_label)
        bar.addWidget(self.mode_badge)
        bar.addWidget(self.bot_state_label)
        bar.addPermanentWidget(styled_label(f"v{__version__}", "status"))
        bar.addPermanentWidget(self.kill_switch)
        self.setStatusBar(bar)

    def _nav_slot(self, page_id: str) -> Callable[[], None]:
        def slot() -> None:
            self.show_page(page_id)

        return slot

    def _add_page(self, page_id: str, widget: QWidget) -> None:
        self._page_index[page_id] = self.pages.addWidget(widget)

    def _to_simple(self) -> None:
        self.set_view_mode(ViewMode.SIMPLE)

    def _persist(self) -> None:
        if self._prefs_dir is not None:
            save_prefs(self._prefs_dir, self.prefs)
