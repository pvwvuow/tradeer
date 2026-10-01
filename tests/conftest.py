"""Shared test configuration."""

import os
from typing import Any

# Headless Qt for CI and local runs; must be set before any Qt module is imported.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ANNOTATION_CHARS = 12_000
MAX_ANNOTATIONS = 4
TRACEBACK_TAIL_CHARS = 3_000


def _workflow_escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def pytest_terminal_summary(terminalreporter: Any) -> None:
    """On GitHub Actions, repeat failures as annotations, so they are readable without the log."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    stats = terminalreporter.stats
    reports = [*stats.get("failed", []), *stats.get("error", [])]
    if not reports:
        return
    chunks = [""]
    for report in reports:
        tail = report.longreprtext[-TRACEBACK_TAIL_CHARS:]
        block = f"{report.nodeid} ({report.when})\n{tail}\n\n"
        if chunks[-1] and len(chunks[-1]) + len(block) > ANNOTATION_CHARS:
            chunks.append("")
        chunks[-1] += block
    for index, chunk in enumerate(chunks[:MAX_ANNOTATIONS], start=1):
        title = f"pytest {len(reports)} failed (part {index} of {len(chunks)})"
        message = _workflow_escape(chunk[:ANNOTATION_CHARS])
        terminalreporter.write_line(f"::error title={title}::{message}")
