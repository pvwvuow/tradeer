"""The Strategies page (spec F3): one card per strategy with on/off, its rules, the
auto-generated settings form, and the signal filters.

Settings are saved per profile in `strategies.json` and used from the next closed bar.
Every change is in the audit log with the values before and after.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core.param_fields import ParamField, param_fields, validate_params
from app.core.strategy_settings import StrategyEntry, StrategySettingsSource
from app.engine.filters import FilterSettings
from app.observability.logger import audit
from app.strategies.base import EXAMPLE_NOTE
from app.strategies.registry import STRATEGIES
from app.ui.pages import PAGE_MARGIN, card_frame, styled_label

INT_LIMIT = 1_000_000
FLOAT_LIMIT = 1e9

Editor = QSpinBox | QDoubleSpinBox | QCheckBox | QComboBox | QLineEdit


def _editor(spec: ParamField, value: Any) -> Editor:
    if spec.kind == "bool":
        box = QCheckBox()
        box.setChecked(bool(value))
        return box
    if spec.kind == "choice":
        combo = QComboBox()
        combo.addItems(list(spec.choices))
        combo.setCurrentText(str(value))
        return combo
    if spec.kind == "int":
        spin = QSpinBox()
        low = int(spec.minimum) if spec.minimum is not None else -INT_LIMIT
        high = int(spec.maximum) if spec.maximum is not None else INT_LIMIT
        spin.setRange(low, high)
        spin.setValue(int(value))
        return spin
    if spec.kind == "float":
        number = QDoubleSpinBox()
        number.setDecimals(3)
        lowest = spec.minimum if spec.minimum is not None else -FLOAT_LIMIT
        highest = spec.maximum if spec.maximum is not None else FLOAT_LIMIT
        number.setRange(lowest, highest)
        number.setSingleStep(spec.step())
        number.setValue(float(value))
        return number
    line = QLineEdit(str(value))
    if spec.pattern:
        line.setPlaceholderText("HH:MM" if ":" in spec.pattern else "")
    return line


def _read(editor: Editor) -> Any:
    if isinstance(editor, QCheckBox):
        return editor.isChecked()
    if isinstance(editor, QComboBox):
        return editor.currentText()
    if isinstance(editor, QSpinBox | QDoubleSpinBox):
        return editor.value()
    return editor.text().strip()


class ParamsForm(QWidget):
    """Inputs for every field of a settings model, with the model's limits."""

    def __init__(self, model: type[BaseModel], values: Mapping[str, Any]) -> None:
        super().__init__()
        self.model = model
        self.editors: dict[str, Editor] = {}
        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for spec in param_fields(model):
            editor = _editor(spec, values.get(spec.name, spec.default))
            editor.setObjectName(f"param_{spec.name}")
            editor.setToolTip(spec.name)
            self.editors[spec.name] = editor
            layout.addRow(spec.label, editor)

    def values(self) -> dict[str, Any]:
        return {name: _read(editor) for name, editor in self.editors.items()}

    def validated(self) -> tuple[BaseModel | None, list[str]]:
        return validate_params(self.model, self.values())


class StrategyCard(QFrame):
    def __init__(self, name: str, entry: StrategyEntry) -> None:
        super().__init__()
        kind = STRATEGIES[name]
        self.name = name
        self.setObjectName(f"strategy_{name}")
        self.setProperty("role", "card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        top = QHBoxLayout()
        top.addWidget(styled_label(f"{kind.title} {kind.version}", "brand"))
        top.addStretch(1)
        self.enabled = QCheckBox("On")
        self.enabled.setObjectName(f"enabled_{name}")
        self.enabled.setChecked(entry.enabled)
        top.addWidget(self.enabled)
        layout.addLayout(top)
        sessions = " and ".join(kind.sessions) or "any session"
        facts = f"Entry timeframe {kind.entry_timeframe}; new entries in {sessions}."
        layout.addWidget(styled_label(kind.description, "muted", wrap=True))
        layout.addWidget(styled_label(facts, "muted", wrap=True))
        layout.addWidget(styled_label(EXAMPLE_NOTE, "status", wrap=True))
        defaults = kind.params_model().model_dump(mode="json")
        self.form = ParamsForm(kind.params_model, {**defaults, **entry.params})
        layout.addWidget(self.form)


class StrategiesPage(QWidget):
    def __init__(
        self,
        source: StrategySettingsSource | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("page_strategies")
        self.source = source
        settings = source.settings if source is not None else None
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
        layout.addWidget(styled_label("Strategies", "title"))
        intro = (
            "Both strategies are examples with exact rules, not proven to be profitable. "
            "They only create signals; nothing is traded. Changes apply from the next closed bar."
        )
        layout.addWidget(styled_label(intro, "muted", wrap=True))
        self.status = styled_label("", "muted", wrap=True)
        self.cards: dict[str, StrategyCard] = {}
        for name in STRATEGIES:
            entry = settings.entry(name) if settings is not None else StrategyEntry()
            card = StrategyCard(name, entry)
            self.cards[name] = card
            layout.addWidget(card)
        filters_card, filters_layout = card_frame()
        filters_layout.addWidget(styled_label("Signal filters", "brand"))
        current = settings.filters if settings is not None else FilterSettings()
        self.filters_form = ParamsForm(FilterSettings, current.model_dump(mode="json"))
        filters_layout.addWidget(self.filters_form)
        layout.addWidget(filters_card)
        row = QHBoxLayout()
        self.save_button = QPushButton("Save strategy settings")
        self.save_button.setObjectName("SaveStrategies")
        self.save_button.clicked.connect(self.save)
        self.save_button.setEnabled(source is not None)
        row.addWidget(self.save_button)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(self.status)
        layout.addStretch(1)

    def save(self) -> bool:
        """Validate every form; save only when all of them are valid."""
        if self.source is None:
            return False
        before = self.source.settings
        problems: list[str] = []
        settings = before
        for name, card in self.cards.items():
            params, errors = card.form.validated()
            problems.extend(f"{STRATEGIES[name].title}: {text}" for text in errors)
            if params is not None:
                values = params.model_dump(mode="json")
                entry = StrategyEntry(enabled=card.enabled.isChecked(), params=values)
                settings = settings.with_entry(name, entry)
        filters, errors = self.filters_form.validated()
        problems.extend(f"Filters: {text}" for text in errors)
        if problems or not isinstance(filters, FilterSettings):
            self.status.setText("Not saved. " + " ".join(problems))
            return False
        settings = settings.with_filters(filters)
        self.source.save(settings)
        old, new = before.model_dump(mode="json"), settings.model_dump(mode="json")
        audit("strategy settings changed", before=old, after=new)
        on = ", ".join(settings.enabled()) or "none"
        self.status.setText(f"Saved. Strategies on: {on}. Used from the next closed bar.")
        return True
