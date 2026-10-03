"""Page building blocks: page headers, cards, empty states and the placeholder page.

Placeholder pages are honest: no sample data, no trading.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QBoxLayout, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.ui.navigation import PageSpec, page_by_id

PAGE_MARGIN = 28
NO_DATA_NOTE = "This page is an empty shell. It never shows sample or simulated data."


def styled_label(text: str, role: str, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", role)
    label.setWordWrap(wrap)
    return label


def card_frame() -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setProperty("role", "card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(8)
    return card, layout


class PageHeader(QWidget):
    """The title row of a page: a small group label, the title and a one-line summary.

    Buttons that act on the whole page go on the right with `add_action`.
    """

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        crumb: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PageHeader")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 4)
        row.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.crumb = styled_label(crumb.upper(), "crumb")
        self.crumb.setVisible(bool(crumb))
        self.title = styled_label(title, "title")
        self.subtitle = styled_label(subtitle, "subtitle", wrap=True)
        self.subtitle.setVisible(bool(subtitle))
        text.addWidget(self.crumb)
        text.addWidget(self.title)
        text.addWidget(self.subtitle)
        row.addLayout(text, 1)
        self.action_row = QHBoxLayout()
        self.action_row.setSpacing(8)
        row.addLayout(self.action_row)

    def add_action(self, widget: QWidget) -> None:
        self.action_row.addWidget(widget, 0, Qt.AlignmentFlag.AlignBottom)


def page_header(title: str, subtitle: str = "", crumb: str = "") -> PageHeader:
    return PageHeader(title, subtitle, crumb)


def page_header_for(page_id: str) -> PageHeader:
    """The header of a registered page: its group, title and summary from the registry."""
    spec = page_by_id(page_id)
    crumb = "" if spec.group == "Simple" else spec.group
    return PageHeader(spec.title, spec.summary, crumb)


def decorate_page(page: QWidget, spec: PageSpec) -> bool:
    """Swap a page's plain title label for the full header (group, title and summary).

    Pages keep building a simple `styled_label(title, "title")`; the main window upgrades it
    here so every page gets the same header without each page knowing about it.
    """
    for label in page.findChildren(QLabel):
        if label.property("role") != "title" or label.text() != spec.title:
            continue
        parent = label.parentWidget()
        if isinstance(parent, PageHeader):
            return True
        layout = parent.layout() if parent is not None else None
        if not isinstance(layout, QBoxLayout):
            return False
        index = layout.indexOf(label)
        if index < 0:
            return False
        crumb = "" if spec.group == "Simple" else spec.group
        layout.insertWidget(index, PageHeader(spec.title, spec.summary, crumb))
        layout.removeWidget(label)
        label.hide()
        label.deleteLater()
        return True
    return False


def empty_state(title: str, text: str, icon: str = "") -> QFrame:
    """A dashed card that says plainly why a section is empty and what fills it."""
    frame = QFrame()
    frame.setProperty("role", "empty")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(24, 28, 24, 28)
    layout.setSpacing(6)
    layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    if icon:
        glyph = styled_label(icon, "empty_icon")
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(glyph)
    heading = styled_label(title, "heading")
    heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
    body = styled_label(text, "muted", wrap=True)
    body.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(heading)
    layout.addWidget(body)
    return frame


def _page_layout(widget: QWidget) -> QVBoxLayout:
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN - 4, PAGE_MARGIN, PAGE_MARGIN)
    layout.setSpacing(16)
    return layout


class PlaceholderPage(QWidget):
    def __init__(self, spec: PageSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.spec = spec
        self.setObjectName(f"page_{spec.page_id}")
        layout = _page_layout(self)
        crumb = "" if spec.group == "Simple" else spec.group
        layout.addWidget(PageHeader(spec.title, spec.summary, crumb))
        self.empty = empty_state(f"Coming in Phase {spec.phase}", NO_DATA_NOTE)
        layout.addWidget(self.empty)
        layout.addStretch(1)
