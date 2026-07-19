"""Regression tests for observability propagation (found by the M10 demo).

The end-to-end demo surfaced three gaps where a run's errors/tool_calls/steps
didn't reach the final RunResult. These lock in the fixes:
  1. SupervisorAgent aggregates each worker's errors/tool_calls/steps.
  2. Agent(inner_base_agent) merges the wrapped agent's observability.
  3. EvalRunner merges the case's metadata (e.g. expected_tools) into scoring.
"""

from __future__ import annotations

from typing import Any

from agentargus import (
    Agent,
    EvalDataset,
    EvalRunner,
    EvalSuite,
    Handoff,
    SupervisorAgent,
    ToolUseAccuracy,
    record_tool_call,
)
from agentargus.core import RunResult


class FakeRouter:
    def route(self, input: Any, workers: dict[str, Any]) -> str:
        return "a"


def test_supervisor_aggregates_worker_observability() -> None:
    def a(x: Any) -> Handoff:
        record_tool_call("search", success=True)
        return Handoff(target="b", input=x)

    def b(x: Any) -> str:
        record_tool_call("analyze", success=True)
        return "done"

    sup = SupervisorAgent({"a": Agent(a, name="a"), "b": Agent(b, name="b")}, router=FakeRouter())
    result = sup.run("q")
    names = [t.name for t in result.tool_calls]
    assert "search" in names and "analyze" in names  # both workers' calls surfaced


def test_agent_merges_wrapped_base_agent_observability() -> None:
    def a(x: Any) -> str:
        record_tool_call("search", success=True)
        return "done"

    sup = SupervisorAgent({"a": Agent(a, name="a")}, router=FakeRouter())
    outer = Agent(sup, name="outer")  # Agent wrapping a BaseAgent
    result = outer.run("q")
    # The inner supervisor's tool_calls must appear on the OUTER result.
    assert [t.name for t in result.tool_calls] == ["search"]


def test_eval_runner_merges_case_metadata() -> None:
    def agent_fn(q: str) -> str:
        record_tool_call("web_search", success=True)
        return "answer"

    dataset = EvalDataset().load(
        [{"question": "q", "metadata": {"expected_tools": ["web_search"]}}]
    )
    report = EvalRunner().run(Agent(agent_fn), dataset, EvalSuite([ToolUseAccuracy()]))
    # expected_tools from case.metadata reached the metric -> exact match = 1.0.
    assert report.case_results[0].scores["tool_use_accuracy"] == 1.0


def test_wrapped_callable_has_no_inner_result_leak() -> None:
    # A plain-callable Agent must not accidentally carry a stale _inner_result.
    result: RunResult = Agent(lambda x: x).run("x")
    assert result.tool_calls == ()
