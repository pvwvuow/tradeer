"""The AI Lab's usage ledger (docs/NOCURVE_V2.md 20e3): one line per request, the month,
today, the last 7 days, unknown prices, broken lines and the clean-up."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

from app.ai.usage_store import (
    DAY,
    KEEP_DAYS,
    USAGE_FILE,
    UsageRecord,
    UsageStore,
    short_tokens,
    summarize,
)

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=UTC).timestamp()  # a Friday


def test_the_summary_counts_the_month_today_and_the_week(tmp_path: Path) -> None:
    store = UsageStore(tmp_path)
    store.add(UsageRecord(NOW - 60, "chat", "grok-test", 1200, 200, 300, 2, 0.004))
    store.add(UsageRecord(NOW - DAY, "ask", "gpt-4o-mini", 3410, 0, 612))
    store.add(UsageRecord(NOW - 12 * DAY, "chat", "old-model", 99, 0, 1, 1, 0.5))  # September
    summary = store.summary(NOW)
    assert summary.month_input == 1200 + 3410 and summary.month_output == 300 + 612
    assert math.isclose(summary.month_cost, 0.004) and summary.unknown_cost == 1
    assert summary.month_requests == 2 and math.isclose(summary.today_cost, 0.004)
    assert [count for _, count in summary.days] == [0, 0, 0, 0, 0, 1, 1]
    assert summary.days[-1][0] == "F" and summary.model == "grok-test"
    assert summary.busiest == 1
    assert summarize([], NOW, fa=True).days[-1] == ("ج", 0)


def test_no_text_is_written_and_broken_lines_are_skipped(tmp_path: Path) -> None:
    store = UsageStore(tmp_path)
    store.add(UsageRecord(NOW, "chat", "m", 10, 0, 5))
    with (tmp_path / USAGE_FILE).open("a", encoding="utf-8") as file:
        file.write("{broken\n[1, 2]\n")
    raw = (tmp_path / USAGE_FILE).read_text(encoding="utf-8")
    assert '"cost": null' in raw and "question" not in raw
    records = store.records()
    assert len(records) == 1 and math.isnan(records[0].cost)


def test_old_lines_are_dropped_and_tokens_are_short(tmp_path: Path) -> None:
    store = UsageStore(tmp_path)
    store.add(UsageRecord(NOW - (KEEP_DAYS + 1) * DAY, "chat", "m"))
    store.add(UsageRecord(NOW, "chat", "m"))
    assert store.compact(NOW) == 1 and len(store.records()) == 1
    assert short_tokens(128_400) == "128.4k" and short_tokens(950) == "950"
    assert short_tokens(2_100_000) == "2.1M"
