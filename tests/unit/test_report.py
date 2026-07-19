"""Tests for EvalReport (spec §8: summary, regressions, valid HTML)."""

from __future__ import annotations

from agentargus.core import CostBreakdown, RunResult
from agentargus.eval import EvalReport
from agentargus.eval.dataset import EvalCase
from agentargus.eval.runner import CaseResult


def _cr(q: str, scores: dict[str, float], cost: float = 0.0) -> CaseResult:
    rr = RunResult(output="a", trace_id="t", cost=CostBreakdown(input_cost=cost))
    return CaseResult(case=EvalCase(question=q), result=rr, scores=scores)


class TestSummary:
    def test_means_and_meta(self) -> None:
        report = EvalReport(
            [
                _cr("q1", {"faithfulness": 1.0}, cost=0.01),
                _cr("q2", {"faithfulness": 0.5}, cost=0.02),
            ]
        )
        s = report.summary()
        assert s["faithfulness"] == 0.75
        assert s["_cases"] == 2.0
        assert s["_failures"] == 0.0
        assert round(s["_total_cost_usd"], 4) == 0.03

    def test_failure_counted(self) -> None:
        good = _cr("q1", {"faithfulness": 1.0})
        bad = CaseResult(case=EvalCase(question="q2"), result=None, error="Boom: x")
        s = EvalReport([good, bad]).summary()
        assert s["_failures"] == 1.0


class TestRegressions:
    def test_flags_worsened_metric(self) -> None:
        baseline = {"faithfulness": 0.85}
        current = EvalReport([_cr("q", {"faithfulness": 0.70})])
        flagged = current.regressions(baseline, threshold=0.05)
        assert "faithfulness" in flagged
        assert flagged["faithfulness"] < 0

    def test_does_not_flag_stable_metric(self) -> None:
        baseline = {"faithfulness": 0.85}
        current = EvalReport([_cr("q", {"faithfulness": 0.83})])
        assert current.regressions(baseline, threshold=0.05) == {}

    def test_baseline_can_be_another_report(self) -> None:
        base = EvalReport([_cr("q", {"faithfulness": 0.9})])
        cur = EvalReport([_cr("q", {"faithfulness": 0.6})])
        assert "faithfulness" in cur.regressions(base)


class TestHtml:
    def test_produces_valid_nonempty_html(self) -> None:
        report = EvalReport([_cr("What is X?", {"faithfulness": 1.0}, cost=0.01)])
        html = report.to_html()
        assert html.strip().startswith("<!doctype html>")
        assert "AgentArgus" in html
        assert "faithfulness" in html
        assert "What is X?" in html
        assert "</html>" in html

    def test_html_shows_regressions_when_baseline_given(self) -> None:
        report = EvalReport([_cr("q", {"faithfulness": 0.6})])
        html = report.to_html(baseline={"faithfulness": 0.9})
        assert "Regressions" in html

    def test_html_marks_failed_case(self) -> None:
        bad = CaseResult(case=EvalCase(question="q"), result=None, error="Boom: x")
        html = EvalReport([bad]).to_html()
        assert "Boom: x" in html


class TestToDict:
    def test_structure(self) -> None:
        d = EvalReport([_cr("q", {"faithfulness": 1.0})]).to_dict()
        assert d["summary"]["faithfulness"] == 1.0
        assert d["cases"][0]["question"] == "q"
