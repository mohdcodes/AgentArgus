"""Tests for RunResult and supporting dataclasses (spec §8: immutability, round-trip)."""

from __future__ import annotations

import dataclasses

import pytest
from agentargus.core import CostBreakdown, RunResult, Span


class TestImmutability:
    def test_top_level_frozen(self, sample_run_result: RunResult) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            sample_run_result.output = "changed"  # type: ignore[misc]

    def test_collections_are_tuples_not_lists(self, sample_run_result: RunResult) -> None:
        # Passed as lists in the fixture; stored as tuples => cannot .append().
        assert isinstance(sample_run_result.spans, tuple)
        assert isinstance(sample_run_result.tool_calls, tuple)
        assert isinstance(sample_run_result.steps, tuple)
        assert isinstance(sample_run_result.errors, tuple)
        with pytest.raises(AttributeError):
            sample_run_result.spans.append(None)  # type: ignore[attr-defined]

    def test_span_attributes_are_read_only(self) -> None:
        span = Span(name="x", span_id="s", start_time=0.0, end_time=1.0, attributes={"a": 1})
        with pytest.raises(TypeError):
            span.attributes["a"] = 2  # type: ignore[index]

    def test_scores_mapping_read_only(self, sample_run_result: RunResult) -> None:
        with pytest.raises(TypeError):
            sample_run_result.scores["x"] = 1.0  # type: ignore[index]


class TestWithScores:
    def test_returns_new_object(self, sample_run_result: RunResult) -> None:
        updated = sample_run_result.with_scores({"faithfulness": 0.9})
        assert updated is not sample_run_result
        assert updated.scores["faithfulness"] == 0.9

    def test_original_unchanged(self, sample_run_result: RunResult) -> None:
        _ = sample_run_result.with_scores({"faithfulness": 0.9})
        assert "faithfulness" not in sample_run_result.scores

    def test_merges_with_existing(self, sample_run_result: RunResult) -> None:
        first = sample_run_result.with_scores({"a": 1.0})
        second = first.with_scores({"b": 2.0})
        assert second.scores == {"a": 1.0, "b": 2.0}


class TestSerialization:
    def test_round_trip(self, sample_run_result: RunResult) -> None:
        data = sample_run_result.to_dict()
        restored = RunResult.from_dict(data)
        assert restored.output == sample_run_result.output
        assert restored.trace_id == sample_run_result.trace_id
        assert restored.cost.total_tokens == sample_run_result.cost.total_tokens
        assert restored.tool_calls[0].name == "search"
        assert restored.spans[0].attributes["gen_ai.system"] == "anthropic"
        assert restored.errors[0].recovered is True

    def test_round_trip_is_json_safe(self, sample_run_result: RunResult) -> None:
        import json

        json.dumps(sample_run_result.to_dict())  # must not raise


class TestCostBreakdown:
    def test_totals(self) -> None:
        c = CostBreakdown(input_tokens=10, output_tokens=5, input_cost=0.1, output_cost=0.2)
        assert c.total_tokens == 15
        assert c.total_cost == pytest.approx(0.3)

    def test_add(self) -> None:
        a = CostBreakdown(input_tokens=10, input_cost=0.1)
        b = CostBreakdown(output_tokens=5, output_cost=0.2)
        total = a + b
        assert total.total_tokens == 15
        assert total.total_cost == pytest.approx(0.3)
