"""Persian for the Simple view (spec A, F1: UI language English + Persian, RTL, Vazirmatn).

The English texts stay the source: the Home model builds them (one test checks them for
jargon) and a `Translator` turns each finished text into Persian with the templates of
`app.ui.i18n_fa`, for example "You could lose about {amount} if it goes wrong". A field's
value is translated again, so "Gold (XAUUSD)" inside a sentence becomes Persian as well. A
text without a template stays as it is, which is how numbers, prices and symbols stay
readable. The Advanced pages stay English (left to right) in this version.

A new language is used from the next start of the app.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import cache
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QAbstractButton, QLabel, QWidget

from app.core.ui_prefs import Language
from app.ui.i18n_fa import PERSIAN

FONT_DIR = Path(__file__).resolve().parent / "fonts"
FONT_FILE = "Vazirmatn.ttf"  # downloaded by scripts/ci/build.ps1 (SIL Open Font License)
PERSIAN_FAMILIES: tuple[str, ...] = ("Vazirmatn", "Segoe UI", "Tahoma")
LANGUAGE_BUTTON: dict[Language, str] = {Language.EN: "فارسی", Language.FA: "English"}
RESTART_TEXT = (
    "The new language is used the next time the app starts: close the app and open it "
    "again.\n\nزبان جدید از دفعه بعد که برنامه باز شود به کار می‌رود: برنامه را ببندید و "
    "دوباره باز کنید."
)
MAX_DEPTH = 3
ENDINGS: dict[str, str] = {".": ".", "?": "\u061f", ":": ":"}
_FIELD = re.compile(r"\{(\w+)\}")


def fields(template: str) -> list[str]:
    return _FIELD.findall(template)


def compile_template(template: str) -> re.Pattern[str]:
    """A template such as "Today: {result}" as a full-match pattern with named groups."""
    parts: list[str] = []
    last = 0
    for match in _FIELD.finditer(template):
        parts.append(re.escape(template[last : match.start()]))
        parts.append(f"(?P<{match.group(1)}>.+?)")
        last = match.end()
    parts.append(re.escape(template[last:]))
    return re.compile("".join(parts), re.DOTALL)


class Translator:
    """English source text to the chosen language; English needs no table."""

    def __init__(self, language: Language, table: Mapping[str, str] | None = None) -> None:
        self.language = language
        found: dict[str, str] = {}
        if language is Language.FA:
            found = dict(PERSIAN if table is None else table)
        self._exact = {key: value for key, value in found.items() if not fields(key)}
        templates = [(key, value) for key, value in found.items() if fields(key)]
        # The most literal text first, so "{hours} h {minutes} min" wins over "{minutes} min".
        templates.sort(key=lambda pair: len(_FIELD.sub("", pair[0])), reverse=True)
        self._templates = [(compile_template(key), value) for key, value in templates]

    @property
    def active(self) -> bool:
        return bool(self._exact or self._templates)

    @property
    def right_to_left(self) -> bool:
        return self.language is Language.FA

    def text(self, text: str) -> str:
        """The translated text; a text of several lines is translated line by line."""
        if not self.active or not text:
            return text
        return "\n".join(self._line(line, 0) for line in text.split("\n"))

    def _line(self, text: str, depth: int) -> str:
        if not text.strip():
            return text
        found = self._exact.get(text)
        if found is not None:
            return found
        for pattern, target in self._templates:
            match = pattern.fullmatch(text)
            if match is None:
                continue
            values = {
                name: self._line(value, depth + 1) if depth < MAX_DEPTH else value
                for name, value in match.groupdict().items()
            }
            return target.format(**values)
        ending = ENDINGS.get(text[-1])
        if ending is not None and len(text) > 1:
            inner = text[:-1]
            translated = self._line(inner, depth)
            if translated != inner:
                return translated + ending
        return text


def translate_widgets(translator: Translator, root: QWidget) -> int:
    """Translate the texts and tooltips under `root` in place; returns how many changed."""
    if not translator.active:
        return 0
    changed = 0
    for widget in [root, *root.findChildren(QWidget)]:
        if isinstance(widget, QLabel | QAbstractButton):
            text = widget.text()
            translated = translator.text(text)
            if translated != text:
                widget.setText(translated)
                changed += 1
        tip = widget.toolTip()
        if tip:
            translated = translator.text(tip)
            if translated != tip:
                widget.setToolTip(translated)
                changed += 1
    return changed


@cache
def load_persian_font() -> bool:
    """Register the bundled Vazirmatn font once; False when it is not bundled (source runs)."""
    path = FONT_DIR / FONT_FILE
    if not path.is_file():
        return False
    return QFontDatabase.addApplicationFont(str(path)) >= 0


def persian_font(base: QFont) -> QFont:
    """Vazirmatn, else the Persian letters of Segoe UI or Tahoma (on every Windows PC)."""
    load_persian_font()
    font = QFont(base)
    font.setFamilies(list(PERSIAN_FAMILIES))
    return font
