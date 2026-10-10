# AI Lab v4: a chat like ChatGPT, answers drawn as reports

The owner's ask (10 Oct): the AI Lab chat still feels weak. It needs Copy, the answers use
an old structure (plain Markdown, Qt's default tables, chart cards only under the answer),
and the AI itself should pick the right visual for each part of an answer.

## 1. The chat (0.45.0)

- Your message sits at the end of the line in a soft round bubble; Copy shows on hover.
- The answer has no bubble: the AI mark, then the answer drawn as a report, then a quiet row
  with **Copy** (the answer's Markdown), **Retry** (ask the same question again) and the cost.
- The folded line "Worked 11 s, 5 steps" stays; the steps open as a timeline.
- A round button jumps to the latest message when you scrolled up.
- The message box is round and grows with the text (up to about 8 lines).

## 2. Answers drawn as reports (0.45.0)

The agent still answers in Markdown (one protocol for every model). `app.ai.answer_blocks`
reads it into blocks and `app.ui.answer_view` draws each one with the theme:

| Block | Written as | Drawn as |
|---|---|---|
| Text, lists | Markdown | text that can be selected |
| Heading | `###` | a bold line |
| Table | GitHub table | captions, hairlines, numbers at the end, +/- green and red, Copy |
| Callout | `> [!WARNING] title` (TIP, CAUTION, NOTE) | a tinted box with a mark |
| Key numbers | fenced `metrics`: `label \| value \| tone \| note` | tiles, up to 4 in a row |
| Chart | fenced `chart line\|area\|bar\|donut`: `label \| number` | a small chart |
| Next steps | fenced `steps`: one step per line | numbered circles |
| A limit used | fenced `progress`: `label \| used \| limit \| note` | meters |
| App chart | fenced `app-chart`: `stats`, `candles EURUSD` | the chart card, in place |
| Code | fenced code | mono, with Copy |

The rule for the agent (`VISUAL_GUIDE`, part of its system prompt): use a block only when it
makes the answer clearer, most answers need none or one or two, every number comes from a
tool result, start with the answer in one or two sentences. A block that cannot be read is
shown as code, so nothing is lost.

## 3. Next (0.45.x)

- Edit a question and ask again; stop and keep the partial answer.
- Answers that stream as they are written, when the provider supports it.
- The chart tool's slow charts (candles, the parameter map) in their place too.
- Export an answer as an image or a Markdown file.
