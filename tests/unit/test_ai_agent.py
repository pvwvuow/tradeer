"""The AI Lab agent loop (docs/AI_LAB_AGENT.md section 2, Phase 18b/18c): one JSON object
per reply, read-only tools, every step reported, and it always ends: with an answer, at
the step or cost limit, on a failed call or when stopped."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.ai.agent import Limits, Step, Tool, cap, history_messages, parse_reply, run_turn
from app.ai.cost import Usage
from app.ai.providers import ApiStyle
from app.ai.transport import AiCallError, CallOptions, Completion, FailureKind, OutputFormat

OPTIONS = CallOptions(ApiStyle.CHAT, OutputFormat.TEXT)


def reply(text: str, cost: float = 0.001) -> Completion:
    usage = Usage(input_tokens=100, cached_tokens=20, output_tokens=30)
    return Completion(text, None, "grok-test", usage, cost, 0.1, OPTIONS, 1)


class Model:
    """Answers with the given texts in turn and keeps every request."""

    def __init__(self, *texts: str, cost: float = 0.001) -> None:
        self.texts = list(texts)
        self.cost = cost
        self.requests: list[list[dict[str, str]]] = []

    def __call__(self, messages: Sequence[Mapping[str, str]]) -> Completion:
        self.requests.append([dict(message) for message in messages])
        text = self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]
        return reply(text, self.cost)


def tool_call(name: str, **args: Any) -> str:
    return json.dumps({"thinking": f"I look at {name}.", "tool": name, "args": args})


def answer(text: str) -> str:
    return json.dumps({"thinking": "I have enough.", "answer": text})


def counting_tool(calls: list[Mapping[str, Any]]) -> Tool:
    def run(args: Mapping[str, Any]) -> str:
        calls.append(args)
        return "12 trades, win rate 67%"

    return Tool("overview", "the overview", "", run, "Read the overview")


def test_a_tool_then_an_answer() -> None:
    calls: list[Mapping[str, Any]] = []
    model = Model(tool_call("overview", days=30), answer("You won **8 of 12**."))
    seen: list[Step] = []
    turn = run_turn(model, [counting_tool(calls)], "How did I do?", on_step=seen.append)
    assert turn.answer == "You won **8 of 12**." and turn.stopped == ""
    assert calls == [{"days": 30}] and turn.calls == 2 and turn.tool_steps == 1
    assert [step.kind for step in turn.steps] == ["thinking", "tool", "thinking"]
    assert list(turn.steps) == seen
    assert seen[1].line().startswith("\u2713 Read the overview (days 30)")
    system, question = model.requests[0][0], model.requests[0][1]
    assert system["role"] == "system" and "- overview(): the overview" in system["content"]
    assert question == {"role": "user", "content": "How did I do?"}
    assert model.requests[1][-1]["content"].startswith("Result of overview:\n12 trades")
    assert turn.usage.input_tokens == 200 and abs(turn.cost - 0.002) < 1e-12
    line = turn.cost_line()
    assert line.startswith("1 step(s) \u00b7 260 tokens (40 cached) \u00b7 $0.002")
    assert line.endswith("grok-test")


def test_plain_text_is_the_answer() -> None:
    turn = run_turn(Model("Just text, no JSON."), [], "Hi")
    assert turn.answer == "Just text, no JSON." and turn.steps == ()
    found = parse_reply('Sure:\n```json\n{"answer": "inside", "thinking": "t"}\n```')
    assert found.answer == "inside" and found.thinking == "t" and not found.tool


def test_the_step_limit_ends_the_turn() -> None:
    calls: list[Mapping[str, Any]] = []
    turn = run_turn(
        Model(tool_call("overview")),
        [counting_tool(calls)],
        "loop",
        limits=Limits(max_steps=2),
    )
    assert len(calls) == 2 and turn.answer == ""
    assert turn.stopped == "stopped at the limit of 2 steps"


def test_the_cost_limit_ends_the_turn() -> None:
    turn = run_turn(
        Model(tool_call("overview"), cost=0.06),
        [counting_tool([])],
        "expensive",
        limits=Limits(max_usd=0.10),
    )
    assert turn.calls == 2 and turn.stopped == "stopped at the cost limit of $0.10"


def test_an_unknown_or_failing_tool_is_told_to_the_model() -> None:
    def broken(args: Mapping[str, Any]) -> str:
        raise ValueError("no data")

    model = Model(tool_call("nope"), tool_call("broken"), answer("done"))
    tools = [Tool("broken", "fails", "", broken)]
    turn = run_turn(model, tools, "q")
    assert turn.answer == "done"
    failed = [step for step in turn.steps if step.kind == "tool"]
    assert [step.ok for step in failed] == [False, False]
    assert failed[0].title == "Unknown tool nope" and failed[0].line().startswith("\u2717")
    assert "There is no tool nope. Tools: broken." in model.requests[1][-1]["content"]
    assert "The tool failed: ValueError: no data" in model.requests[2][-1]["content"]


def test_a_failed_call_and_a_stop_end_the_turn() -> None:
    def refused(messages: Sequence[Mapping[str, str]]) -> Completion:
        raise AiCallError(FailureKind.KEY, "HTTP 401: bad key", 401)

    turn = run_turn(refused, [], "q")
    assert turn.stopped == "HTTP 401: bad key" and turn.steps[0].kind == "error"
    stopped = run_turn(Model(answer("never")), [], "q", cancelled=lambda: True)
    assert stopped.stopped == "stopped by you" and stopped.calls == 0


def test_results_are_capped_and_history_keeps_only_answers() -> None:
    assert cap("x" * 10, 20) == "x" * 10
    assert cap("x" * 30, 20).endswith("... truncated (10 more characters)")
    first = run_turn(Model(answer("one")), [], "first?")
    stopped = run_turn(Model(answer("x")), [], "second?", cancelled=lambda: True)
    assert history_messages([first, stopped]) == [
        {"role": "user", "content": "first?"},
        {"role": "assistant", "content": "one"},
    ]
    model = Model(answer("two"))
    run_turn(model, [], "third?", history=[first], language="fa")
    sent = model.requests[0]
    assert [item["role"] for item in sent] == ["system", "user", "assistant", "user"]
    assert "Answer in Persian" in sent[0]["content"]
