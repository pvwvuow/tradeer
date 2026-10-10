"""The AI's answer as blocks (docs/AI_LAB_V4.md section 2, 0.45.0): Markdown stays text,
tables, alerts, code and the agent's visual blocks (metrics, charts, steps, progress, the
place of an app chart) are read; a visual block that cannot be read stays as code."""

from __future__ import annotations

from app.ai.answer_blocks import (
    VISUAL_GUIDE,
    AppChart,
    Callout,
    Chart,
    Code,
    Heading,
    Metrics,
    Progress,
    Rule,
    Steps,
    Table,
    Text,
    number,
    parse,
    plain,
    tone_of,
)

ANSWER = """You lost **3 of 5** trades this week.

```metrics
Net | -1.2% | | 5 trades
Win rate | 40% | | too few
Max DD | -2.1% | warning
```

### Why
- the Asian session
- wide stops

| Strategy | Trades | Net |
|---|---:|---:|
| trend | 3 | +12.5 |
| range | 2 | -40 |

> [!WARNING] Daily loss close to the limit
> You used 80% of today's loss limit.

```chart bar
title: Net by day
unit: $
x | Net
Mon | 12
Tue | -30
Wed | 5
```

```steps
1. Turn off range on XAUUSD
2. Backtest 90 days
```

```progress
Daily loss | 80 | 100 | of the cap
Open trades | 2 | 3
```

```app-chart
candles eur/usd
```

```python
print(1)
```
---
done"""


def test_an_answer_becomes_its_blocks() -> None:
    blocks = parse(ANSWER)
    kinds = [type(block).__name__ for block in blocks]
    assert kinds == [
        "Text",
        "Metrics",
        "Heading",
        "Text",
        "Table",
        "Callout",
        "Chart",
        "Steps",
        "Progress",
        "AppChart",
        "Code",
        "Rule",
        "Text",
    ]
    assert blocks[0] == Text("You lost **3 of 5** trades this week.")
    metrics = blocks[1]
    assert isinstance(metrics, Metrics)
    assert [(m.label, m.value, m.tone) for m in metrics.items] == [
        ("Net", "-1.2%", "loss"),
        ("Win rate", "40%", ""),
        ("Max DD", "-2.1%", "warning"),
    ]
    assert blocks[2] == Heading("Why", 3)
    table = blocks[4]
    assert isinstance(table, Table) and table.align == ("left", "right", "right")
    assert table.rows[1] == ("range", "2", "-40")
    assert table.tsv().splitlines()[0] == "Strategy\tTrades\tNet"
    assert blocks[5] == Callout(
        "warning",
        "Daily loss close to the limit",
        "You used 80% of today's loss limit.",
    )
    chart = blocks[6]
    assert isinstance(chart, Chart) and chart.style == "bar" and chart.unit == "$"
    assert chart.title == "Net by day" and chart.labels == ("Mon", "Tue", "Wed")
    assert chart.series[0].name == "Net" and chart.series[0].values == (12.0, -30.0, 5.0)
    assert blocks[7] == Steps(("Turn off range on XAUUSD", "Backtest 90 days"))
    progress = blocks[8]
    assert isinstance(progress, Progress) and progress.items[0].share == 0.8
    assert progress.items[0].text == "80 / 100"
    assert blocks[9] == AppChart("candles", "EURUSD")
    assert blocks[10] == Code("print(1)", "python")
    assert isinstance(blocks[11], Rule)


def test_an_unreadable_visual_block_stays_as_code() -> None:
    blocks = parse("```chart line\nno numbers here\n```")
    assert blocks == [Code("no numbers here", "chart")]
    assert parse("```metrics\n\n```") == [Code("", "metrics")]
    donut = parse("```chart donut\nEURUSD | 3\nXAUUSD | 2\n```")[0]
    assert isinstance(donut, Chart) and donut.series[0].name == ""
    two = parse("```chart line\nday | current | AI\n1 | 10 | 11\n2 | 12 | 15\n```")[0]
    assert isinstance(two, Chart) and [s.name for s in two.series] == ["current", "AI"]
    assert two.series[1].values == (11.0, 15.0)


def test_a_plain_quote_and_the_small_helpers() -> None:
    assert parse("> just a quote") == [Callout("quote", "", "just a quote")]
    tip = parse("> [!TIP] Try this\n> Lower the risk.")[0]
    assert tip == Callout("success", "Try this", "Lower the risk.")
    assert number("+3.2%") == 3.2 and number("1,250.50 USD") == 1250.5
    assert number("۱۲") == 12.0 and number("\u22122.5") == -2.5 and number("n/a") is None
    assert tone_of("+5") == "profit" and tone_of("-5") == "loss" and tone_of("5") == ""
    assert tone_of("-5", "warning") == "warning"
    assert plain("**Net** is `+3%`\n### Why\n> [!TIP] x") == "Net is +3%\nWhy\nx"
    assert "```metrics" in VISUAL_GUIDE and "[!WARNING]" in VISUAL_GUIDE
