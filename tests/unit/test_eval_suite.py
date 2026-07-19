"""Tests for EvalSuite (spec §8: aggregation, NaN exclusion, with_scores)."""

from __future__ import annotations

import json

from agentargus import ContextRecall, EvalSuite, Faithfulness, RunResult


class FakeJudge:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def complete(self, prompt: str) -> str:
        return json.dumps(self._payload)


def test_run_aggregates_named_scores() -> None:
    judge = FakeJudge({"claims": ["a"], "supported": [True]})
    suite = EvalSuite([Faithfulness(judge=judge)])
    scores = suite.run({"answer": "a", "contexts": ["c"]})
    assert scores == {"faithfulness": 1.0}


def test_not_applicable_metrics_excluded() -> None:
    judge = FakeJudge({"claims": ["a"], "supported": [True]})
    # ContextRecall with no reference -> NaN -> excluded.
    suite = EvalSuite([Faithfulness(judge=judge), ContextRecall(judge=judge)])
    scores = suite.run({"answer": "a", "contexts": ["c"]})
    assert "faithfulness" in scores
    assert "context_recall" not in scores


def test_score_returns_new_run_result_with_scores() -> None:
    judge = FakeJudge({"claims": ["a"], "supported": [True]})
    rr = RunResult(output="a", trace_id="t", metadata={"question": "q", "contexts": ["c"]})
    scored = EvalSuite([Faithfulness(judge=judge)]).score(rr)
    assert scored is not rr  # immutability
    assert scored.scores["faithfulness"] == 1.0
    assert "faithfulness" not in rr.scores  # original untouched
