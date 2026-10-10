"""The AI's answer as blocks for the chat (docs/AI_LAB_V4.md section 2, 0.45.0).

The agent answers in Markdown. Most of it stays Markdown (`Text`), but the chat draws some
parts itself, so an answer reads like a report and not like a wall of text:

- `Heading`, `Rule`, `Code` (with its language) and `Table` (a GitHub table).
- `Callout`: a GitHub alert in a quote (`> [!WARNING] Title`), or a plain quote.
- Fenced blocks the agent writes when they help (`VISUAL_GUIDE` tells it how):
  `metrics` (2 to 4 key numbers), `chart line|area|bar|donut` (its own small data),
  `steps` (what to do next), `progress` (how much of a limit is used) and `app-chart`
  (where a chart card of the app's own data goes).

A visual block that cannot be read is shown as code, so nothing of the answer is lost.
Pure: no Qt, no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

VISUALS = ("metrics", "chart", "steps", "progress", "app-chart", "callout")
CHART_TYPES = ("line", "area", "bar", "donut")
TONES = ("profit", "loss", "warning", "info", "success", "danger", "")
ALERTS = {
    "NOTE": "info",
    "IMPORTANT": "info",
    "TIP": "success",
    "WARNING": "warning",
    "CAUTION": "danger",
}
MAX_METRICS = 8
MAX_POINTS = 400
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*([^`]*)$")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
ALERT = re.compile(r"^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*(.*)$", re.IGNORECASE)
NUMBER = re.compile(r"[-+\u2212]?\d[\d,]*(?:\.\d+)?|[-+\u2212]?\.\d+")
DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

VISUAL_GUIDE = (
    "Write the answer in Markdown for a chat that draws some blocks itself. Use a block only "
    "when it makes the answer clearer, never as decoration; most answers need none or one or "
    "two. Every number must come from a tool result. Start with the answer in one or two "
    "plain sentences, then the details. Blocks (a fenced block with the name after the "
    "backticks, one item per line, parts split by |):\n"
    "- 2 to 4 key numbers: ```metrics with lines label | value | tone | short note "
    "(tone: profit, loss, warning or empty).\n"
    "- A change over time: ```chart line (or area); a comparison of a few categories: "
    "```chart bar; shares of a whole: ```chart donut. First line x | name of the series, "
    "then one line per point label | number; optional lines title: ... and unit: ...\n"
    "- Several facts with several columns: a Markdown table, at most 8 rows.\n"
    "- A risk or something that is wrong: a quote that starts with [!WARNING] and a short "
    "title, then the text; a tip: [!TIP]; something to stop now: [!CAUTION].\n"
    "- What the user should do next, in order: ```steps with one step per line.\n"
    "- How much of a limit is used: ```progress with lines label | used | limit | note.\n"
    "- After the chart tool: ```app-chart with the kind (and the symbol) where the card "
    "should stand; without it the card comes under the answer.\n"
    "Use ### headings only in long answers. A short question gets a short answer."
)


@dataclass(frozen=True)
class Text:
    markdown: str


@dataclass(frozen=True)
class Heading:
    text: str
    level: int


@dataclass(frozen=True)
class Rule:
    pass


@dataclass(frozen=True)
class Code:
    text: str
    language: str = ""


@dataclass(frozen=True)
class Table:
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    align: tuple[str, ...]  # left, right or center per column

    def tsv(self) -> str:
        """The table for the clipboard (a spreadsheet takes it as it is)."""
        return "\n".join("\t".join(row) for row in (self.header, *self.rows))


@dataclass(frozen=True)
class Callout:
    tone: str  # info, success, warning, danger or quote
    title: str
    text: str


@dataclass(frozen=True)
class Metric:
    label: str
    value: str
    tone: str = ""
    note: str = ""


@dataclass(frozen=True)
class Metrics:
    items: tuple[Metric, ...]


@dataclass(frozen=True)
class Series:
    name: str
    values: tuple[float, ...]


@dataclass(frozen=True)
class Chart:
    style: str  # line, area, bar or donut
    labels: tuple[str, ...]
    series: tuple[Series, ...]
    title: str = ""
    unit: str = ""


@dataclass(frozen=True)
class Steps:
    items: tuple[str, ...]


@dataclass(frozen=True)
class Gauge:
    label: str
    share: float  # 0 to 1
    text: str
    note: str = ""


@dataclass(frozen=True)
class Progress:
    items: tuple[Gauge, ...]


@dataclass(frozen=True)
class AppChart:
    kind: str
    symbol: str = ""


Block = (
    Text | Heading | Rule | Code | Table | Callout | Metrics | Chart | Steps | Progress | AppChart
)


def number(text: str) -> float | None:
    """The first number in a cell ("+3.2%", "1,250.50 USD", "۱۲"), or None."""
    found = NUMBER.search(text.translate(DIGITS))
    if found is None:
        return None
    raw = found.group(0).replace(",", "").replace("\u2212", "-")
    try:
        return float(raw)
    except ValueError:
        return None


def cells(line: str) -> list[str]:
    """The parts of a line split by | (the outer bars of a table row are dropped)."""
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    return [part.strip().replace("\\|", "|") for part in re.split(r"(?<!\\)\|", text)]


def tone_of(value: str, tone: str = "") -> str:
    """The tone of a value: the one given, or profit for "+...", loss for "-..."."""
    given = tone.strip().lower()
    if given in TONES and given:
        return given
    clean = value.strip()
    if clean.startswith("+"):
        return "profit"
    if clean.startswith(("-", "\u2212")) and number(clean) is not None:
        return "loss"
    return ""


def _metrics(lines: Sequence[str]) -> Metrics | None:
    items: list[Metric] = []
    for line in lines:
        parts = cells(line)
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        tone = parts[2] if len(parts) > 2 else ""
        note = parts[3] if len(parts) > 3 else ""
        items.append(Metric(parts[0], parts[1], tone_of(parts[1], tone), note))
    return Metrics(tuple(items[:MAX_METRICS])) if items else None


def _options(lines: Sequence[str]) -> tuple[dict[str, str], list[str]]:
    options: dict[str, str] = {}
    rest: list[str] = []
    for line in lines:
        found = re.match(r"^\s*(title|unit|type)\s*:\s*(.*)$", line, re.IGNORECASE)
        if found and "|" not in line:
            options[found.group(1).lower()] = found.group(2).strip()
        else:
            rest.append(line)
    return options, rest


def _chart(info: str, lines: Sequence[str]) -> Chart | None:
    options, rows = _options(lines)
    words = info.split()
    style = (options.get("type") or (words[1] if len(words) > 1 else "line")).lower()
    if style not in CHART_TYPES:
        style = "bar" if style in ("column", "columns", "bars") else "line"
    table = [cells(line) for line in rows if line.strip() and not TABLE_SEPARATOR.match(line)]
    table = [row for row in table if len(row) >= 2]
    if not table:
        return None
    names: list[str] = []
    if all(number(cell) is None for cell in table[0][1:]):
        names = table[0][1:]
        table = table[1:]
    width = max((len(row) for row in table), default=0)
    count = max(width - 1, 1)
    labels: list[str] = []
    columns: list[list[float]] = [[] for _ in range(count)]
    for row in table[:MAX_POINTS]:
        values = [number(cell) for cell in row[1:]]
        if values[0] is None:
            continue
        labels.append(row[0])
        for index in range(count):
            value = values[index] if index < len(values) else None
            columns[index].append(value if value is not None else 0.0)
    if not labels:
        return None
    series = tuple(
        Series(names[index] if index < len(names) else "", tuple(columns[index]))
        for index in range(count)
    )
    title = options.get("title", " ".join(words[2:]) if len(words) > 2 else "")
    return Chart(style, tuple(labels), series, title, options.get("unit", ""))


def _steps(lines: Sequence[str]) -> Steps | None:
    items = []
    for line in lines:
        text = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line).strip()
        if text:
            items.append(text)
    return Steps(tuple(items)) if items else None


def _progress(lines: Sequence[str]) -> Progress | None:
    items: list[Gauge] = []
    for line in lines:
        parts = cells(line)
        if len(parts) < 2 or not parts[0]:
            continue
        used = number(parts[1])
        if used is None:
            continue
        limit = number(parts[2]) if len(parts) > 2 else None
        note = parts[3] if len(parts) > 3 else ""
        if limit is not None and limit > 0:
            share, text = used / limit, f"{parts[1]} / {parts[2]}"
        elif "%" in parts[1]:
            share, text = used / 100, parts[1]
        else:
            share, text = used, parts[1]
        items.append(Gauge(parts[0], min(max(share, 0.0), 1.0), text, note))
    return Progress(tuple(items)) if items else None


def _app_chart(lines: Sequence[str]) -> AppChart | None:
    for line in lines:
        words = line.split()
        if words:
            symbol = words[1].upper().replace("/", "") if len(words) > 1 else ""
            return AppChart(words[0].strip().lower(), symbol)
    return None


def _callout(info: str, lines: Sequence[str]) -> Callout | None:
    words = info.split()
    tone = words[1].lower() if len(words) > 1 else "info"
    options, rest = _options(lines)
    text = "\n".join(rest).strip()
    if not text and not options.get("title"):
        return None
    tone = {"tip": "success", "caution": "danger", "note": "info"}.get(tone, tone)
    return Callout(tone if tone in TONES else "info", options.get("title", ""), text)


def visual(info: str, lines: Sequence[str]) -> Block | None:
    """A fenced visual block, or None when it cannot be read."""
    name = info.split()[0].lower() if info.split() else ""
    if name == "metrics":
        return _metrics(lines)
    if name == "chart":
        return _chart(info, lines)
    if name == "steps":
        return _steps(lines)
    if name == "progress":
        return _progress(lines)
    if name == "app-chart":
        return _app_chart(lines)
    if name == "callout":
        return _callout(info, lines)
    return None


def _quote(lines: Sequence[str]) -> Callout:
    body = [re.sub(r"^\s*>\s?", "", line) for line in lines]
    first = body[0].strip() if body else ""
    found = ALERT.match(first)
    if found is None:
        return Callout("quote", "", "\n".join(body).strip())
    tone = ALERTS[found.group(1).upper()]
    title = found.group(2).strip()
    return Callout(tone, title, "\n".join(body[1:]).strip())


def _table(lines: Sequence[str]) -> Table:
    header = cells(lines[0])
    marks = cells(lines[1])
    align: list[str] = []
    for mark in marks:
        if mark.startswith(":") and mark.endswith(":"):
            align.append("center")
        elif mark.endswith(":"):
            align.append("right")
        else:
            align.append("left")
    width = len(header)
    rows = []
    for line in lines[2:]:
        row = cells(line)[:width]
        rows.append(tuple(row + [""] * (width - len(row))))
    align = (align + ["left"] * width)[:width]
    return Table(tuple(header), tuple(rows), tuple(align))


def _is_table_start(lines: Sequence[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    return "|" in lines[index] and TABLE_SEPARATOR.match(lines[index + 1]) is not None


def parse(markdown: str) -> list[Block]:
    """The answer as blocks, in order; the Markdown between them stays `Text`."""
    lines = markdown.replace("\r\n", "\n").split("\n")
    blocks: list[Block] = []
    text: list[str] = []

    def flush() -> None:
        joined = "\n".join(text).strip("\n")
        if joined.strip():
            blocks.append(Text(joined))
        text.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        fence = FENCE.match(line)
        if fence is not None:
            mark, info = fence.group(1), fence.group(2).strip()
            body: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith(mark):
                body.append(lines[index])
                index += 1
            index += 1  # the closing fence
            flush()
            name = info.split()[0].lower() if info.split() else ""
            found = visual(info, body) if name in VISUALS else None
            blocks.append(found if found is not None else Code("\n".join(body), name))
            continue
        heading = HEADING.match(line)
        if heading is not None:
            flush()
            blocks.append(Heading(heading.group(2).strip(), len(heading.group(1))))
            index += 1
            continue
        if RULE.match(line) and not (text and text[-1].strip()):
            flush()
            blocks.append(Rule())
            index += 1
            continue
        if _is_table_start(lines, index):
            flush()
            start = index
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                index += 1
            blocks.append(_table(lines[start:index]))
            continue
        if line.lstrip().startswith(">"):
            flush()
            start = index
            while index < len(lines) and lines[index].lstrip().startswith(">"):
                index += 1
            blocks.append(_quote(lines[start:index]))
            continue
        text.append(line)
        index += 1
    flush()
    return blocks


def plain(markdown: str) -> str:
    """The answer as plain text, for a paste where Markdown would show its marks."""
    found = re.sub(r"```[^\n]*\n", "", markdown)
    found = found.replace("```", "")
    found = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), found)
    found = re.sub(r"`([^`]+)`", r"\1", found)
    found = re.sub(r"^\s{0,3}#{1,6}\s+", "", found, flags=re.MULTILINE)
    return re.sub(r"^\s*>\s?(\[![A-Z]+\]\s*)?", "", found, flags=re.MULTILINE).strip()
