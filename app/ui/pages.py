"""Page widgets. Phase 1 pages are honest placeholders: no sample data, no trading."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from app.ui.navigation import PageSpec

PAGE_MARGIN = 32
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
    layout.setContentsMargins(24, 24, 24, 24)
    layout.setSpacing(8)
    return card, layout


def _page_layout(widget: QWidget) -> QVBoxLayout:
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
    layout.setSpacing(16)
    return layout


class PlaceholderPage(QWidget):
    def __init__(self, spec: PageSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.spec = spec
        self.setObjectName(f"page_{spec.page_id}")
        layout = _page_layout(self)
        layout.addWidget(styled_label(spec.title, "title"))
        layout.addWidget(styled_label(spec.summary, "muted", wrap=True))
        card, card_layout = card_frame()
        card_layout.addWidget(styled_label(f"Coming in Phase {spec.phase}", "brand"))
        card_layout.addWidget(styled_label(NO_DATA_NOTE, "muted", wrap=True))
        layout.addWidget(card)
        layout.addStretch(1)
