"""Tests for agent-behaviour metrics (spec §8)."""

from __future__ import annotations

import json
import math

import pytest
from agentargus import (
    Agent,
    ErrorRecoveryRate,
    EvalSuite,
    Faithfulness,
    PlanCoherence,
    ToolSuccessRate,
    ToolUseAccuracy,
)
from agentargus.core import ErrorRecord, Step, ToolCall


def tc(name: str, success: bool = True) -> ToolCall:
    return ToolCall(name=name, args={}, result=None, success=success, latency=0.0)


class TestToolUseAccuracy:
    def test_requires_expected_tools_else_not_applicable(self) -> None:
        score = ToolUseAccuracy().compute({"tool_calls": [tc("search")]})
        assert math.isnan(score)  # NOT_APPLICABLE without a label

    def test_exact_match_scores_one(self) -> None:
        score = ToolUseAccuracy().compute(
            {
                "tool_calls": [tc("web_search"), tc("fetch_page")],
                "expected_tools": ["web_search", "fetch_page"],
            }
        )
        assert score == 1.0

    def test_partial_match_f1(self) -> None:
        # expected {web_search, fetch_page, calculator}, actual {web_search,
        # fetch_page, summarize}: hits=2, P=2/3, R=2/3, F1=2/3.
        score = ToolUseAccuracy().compute(
            {
                "tool_calls": [tc("web_search"), tc("fetch_page"), tc("summarize")],
                "expected_tools": ["web_search", "fetch_page", "calculator"],
            }
        )
        assert score == pytest.approx(2 / 3)

    def test_no_overlap_scores_zero(self) -> None:
        score = ToolUseAccuracy().compute({"tool_calls": [tc("a")], "expected_tools": ["b"]})
        assert score == 0.0


class TestToolSuccessRate:
    def test_all_success(self) -> None:
        assert ToolSuccessRate().compute({"tool_calls": [tc("a"), tc("b")]}) == 1.0

    def test_mixed(self) -> None:
        score = ToolSuccessRate().compute({"tool_calls": [tc("a", True), tc("b", False)]})
        assert score == 0.5

    def test_no_calls_not_applicable(self) -> None:
        assert math.isnan(ToolSuccessRate().compute({"tool_calls": []}))


class TestErrorRecoveryRate:
    def _err(self, recovered: bool) -> ErrorRecord:
        return ErrorRecord(error_type="X", message="m", recovered=recovered)

    def test_all_recovered(self) -> None:
        score = ErrorRecoveryRate().compute({"errors": [self._err(True), self._err(True)]})
        assert score == 1.0

    def test_none_recovered(self) -> None:
        assert ErrorRecoveryRate().compute({"errors": [self._err(False)]}) == 0.0

    def test_no_errors_is_perfect(self) -> None:
        assert ErrorRecoveryRate().compute({"errors": []}) == 1.0


class TestPlanCoherence:
    def _steps(self) -> list[Step]:
        return [
            Step(index=0, kind="reason", content="think"),
            Step(index=1, kind="act", content="do"),
        ]

    def test_judge_scores(self) -> None:
        class J:
            def complete(self, p: str) -> str:
                return json.dumps({"coherence": 0.8})

        score = PlanCoherence(judge=J()).compute({"question": "q", "steps": self._steps()})
        assert score == pytest.approx(0.8)

    def test_no_steps_not_applicable(self) -> None:
        class J:
            def complete(self, p: str) -> str:
                return "{}"

        assert math.isnan(PlanCoherence(judge=J()).compute({"steps": []}))


class TestUnifiedSuite:
    def test_rag_and_agent_metrics_in_one_suite(self) -> None:
        # THE GATE: RAG + agent metrics run together.
        class J:
            def complete(self, p: str) -> str:
                return json.dumps({"claims": ["a"], "supported": [True]})

        source = {
            "question": "q",
            "answer": "a",
            "contexts": ["ctx"],
            "tool_calls": [tc("search", True), tc("fetch", False)],
            "errors": [ErrorRecord(error_type="T", message="m", recovered=True)],
        }
        suite = EvalSuite([Faithfulness(judge=J()), ToolSuccessRate(), ErrorRecoveryRate()])
        scores = suite.run(source)
        assert scores["faithfulness"] == 1.0
        assert scores["tool_success_rate"] == 0.5
        assert scores["error_recovery_rate"] == 1.0


class TestAgentRecorderIntegration:
    def test_recorded_calls_appear_on_run_result(self) -> None:
        from agentargus import record_step, record_tool_call

        def researcher(question: str) -> str:
            record_step("reason", "decide to search")
            record_tool_call("web_search", {"q": question}, ["r1"], success=True)
            record_tool_call("fetch_page", {"url": "r1"}, "text", success=True)
            return "answer"

        result = Agent(researcher).run("what is X?")
        assert [t.name for t in result.tool_calls] == ["web_search", "fetch_page"]
        assert len(result.steps) == 1
        assert result.steps[0].kind == "reason"

    def test_scored_end_to_end(self) -> None:
        def researcher(q: str) -> str:
            from agentargus import record_tool_call

            record_tool_call("web_search", success=True)
            record_tool_call("fetch_page", success=True)
            return "answer"

        result = Agent(researcher).run("q")
        # ToolSuccessRate on the real recorded calls.
        assert ToolSuccessRate().compute(result) == 1.0
