"""Tests for RAG metrics (spec §8: mock judge, high/low, overload, edge cases)."""

from __future__ import annotations

import json
import math

import pytest
from agentargus import (
    AnswerRelevance,
    ContextPrecision,
    ContextRecall,
    Faithfulness,
    RunResult,
)


class FakeJudge:
    """Returns a canned JSON string regardless of prompt."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def complete(self, prompt: str) -> str:
        return json.dumps(self._payload)


class FakeEmbedder:
    """Deterministic tiny embeddings keyed by exact-match to a table."""

    def __init__(self, table: dict[str, list[float]]) -> None:
        self._table = table

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._table.get(t, [0.0, 0.0, 1.0]) for t in texts]


class TestFaithfulness:
    def test_all_supported_scores_high(self) -> None:
        judge = FakeJudge({"claims": ["a", "b"], "supported": [True, True]})
        score = Faithfulness(judge=judge).compute(
            {"question": "q", "answer": "a", "contexts": ["ctx"]}
        )
        assert score == 1.0

    def test_none_supported_scores_low(self) -> None:
        judge = FakeJudge({"claims": ["a", "b"], "supported": [False, False]})
        assert Faithfulness(judge=judge).compute({"answer": "a", "contexts": ["c"]}) == 0.0

    def test_partial(self) -> None:
        judge = FakeJudge({"claims": ["a", "b"], "supported": [True, False]})
        assert Faithfulness(judge=judge).compute({"answer": "a", "contexts": ["c"]}) == 0.5

    def test_no_claims_is_vacuously_faithful(self) -> None:
        judge = FakeJudge({"claims": [], "supported": []})
        assert Faithfulness(judge=judge).compute({"answer": "", "contexts": ["c"]}) == 1.0


class TestAnswerRelevance:
    def test_judge_fallback_path(self) -> None:
        judge = FakeJudge({"relevance": 0.9})
        score = AnswerRelevance(judge=judge).compute({"question": "q", "answer": "a"})
        assert score == pytest.approx(0.9)

    def test_embedding_path(self) -> None:
        # Original question and generated questions all map to the same vector →
        # cosine similarity 1.0.
        vec = [1.0, 0.0, 0.0]
        judge = FakeJudge({"questions": ["gen1", "gen2"]})
        embedder = FakeEmbedder({"q": vec, "gen1": vec, "gen2": vec})
        metric = AnswerRelevance(judge=judge, embedder=embedder, n_questions=2)
        score = metric.compute({"question": "q", "answer": "a"})
        assert score == pytest.approx(1.0)

    def test_embedding_orthogonal_scores_zero(self) -> None:
        judge = FakeJudge({"questions": ["gen1"]})
        embedder = FakeEmbedder({"q": [1.0, 0.0], "gen1": [0.0, 1.0]})
        metric = AnswerRelevance(judge=judge, embedder=embedder, n_questions=1)
        assert metric.compute({"question": "q", "answer": "a"}) == pytest.approx(0.0)


class TestContextPrecision:
    def test_rank_aware_relevant_first_scores_higher(self) -> None:
        # Relevant chunk ranked first (AP = 1.0) beats relevant ranked last.
        judge_first = FakeJudge({"relevant": [True, False]})
        judge_last = FakeJudge({"relevant": [False, True]})
        first = ContextPrecision(judge=judge_first).compute(
            {"question": "q", "contexts": ["c1", "c2"]}
        )
        last = ContextPrecision(judge=judge_last).compute(
            {"question": "q", "contexts": ["c1", "c2"]}
        )
        assert first == pytest.approx(1.0)
        assert last == pytest.approx(0.5)
        assert first > last

    def test_no_relevant_scores_zero(self) -> None:
        judge = FakeJudge({"relevant": [False, False]})
        assert ContextPrecision(judge=judge).compute({"contexts": ["a", "b"]}) == 0.0

    def test_no_contexts_scores_zero(self) -> None:
        judge = FakeJudge({"relevant": []})
        assert ContextPrecision(judge=judge).compute({"contexts": []}) == 0.0


class TestContextRecall:
    def test_requires_reference_else_not_applicable(self) -> None:
        judge = FakeJudge({"claims": ["a"], "supported": [True]})
        score = ContextRecall(judge=judge).compute({"answer": "a", "contexts": ["c"]})
        assert math.isnan(score)  # NOT_APPLICABLE without a reference

    def test_scores_against_reference(self) -> None:
        judge = FakeJudge({"claims": ["a", "b"], "supported": [True, False]})
        score = ContextRecall(judge=judge).compute({"contexts": ["c"], "reference": "ground truth"})
        assert score == pytest.approx(0.5)


class TestMissingJudge:
    def test_clear_error(self) -> None:
        with pytest.raises(ValueError, match="needs a judge"):
            Faithfulness().compute({"answer": "a", "contexts": ["c"]})


class TestTolerantParsing:
    def test_malformed_judge_output_uses_default_not_crash(self) -> None:
        class BadJudge:
            def complete(self, prompt: str) -> str:
                return "not json at all, sorry"

        # Faithfulness default = no claims → 1.0 (conservative, doesn't crash).
        score = Faithfulness(judge=BadJudge()).compute({"answer": "a", "contexts": ["c"]})
        assert score == 1.0

    def test_fenced_json_is_parsed(self) -> None:
        class FencedJudge:
            def complete(self, prompt: str) -> str:
                return '```json\n{"claims": ["x"], "supported": [true]}\n```'

        assert Faithfulness(judge=FencedJudge()).compute({"answer": "a", "contexts": ["c"]}) == 1.0


class TestOverloadInputs:
    def test_accepts_run_result(self) -> None:
        judge = FakeJudge({"claims": ["a"], "supported": [True]})
        rr = RunResult(
            output="the answer",
            trace_id="t",
            metadata={"question": "q", "contexts": ["ctx"]},
        )
        assert Faithfulness(judge=judge).compute(rr) == 1.0

    def test_accepts_dict(self) -> None:
        judge = FakeJudge({"claims": ["a"], "supported": [True]})
        assert Faithfulness(judge=judge).compute({"answer": "x", "contexts": ["c"]}) == 1.0
