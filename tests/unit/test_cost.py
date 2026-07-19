"""Tests for CostTracker (spec §8: cost math, ceiling, ledger, unknown model)."""

from __future__ import annotations

import pytest
from agentargus import Agent, CostTracker, Tracer, Usage
from agentargus._internal.exceptions import CostCeilingExceeded


def make_tracker(**kw: object) -> CostTracker:
    # Claude Opus-style prices: $15 / 1M input, $75 / 1M output.
    return CostTracker(pricing={"claude-opus-4-8": (15.0, 75.0)}, **kw)  # type: ignore[arg-type]


class TestCostMath:
    def test_matches_hand_computed_fixture(self) -> None:
        # 1000 input @ $15/1M = 0.015 ; 500 output @ $75/1M = 0.0375
        tracker = make_tracker()
        tracker.add_usage({"input_tokens": 1000, "output_tokens": 500}, model="claude-opus-4-8")
        total = tracker.total()
        assert total.input_cost == pytest.approx(0.015)
        assert total.output_cost == pytest.approx(0.0375)
        assert total.total_cost == pytest.approx(0.0525)

    def test_within_5_percent_over_many_calls(self) -> None:
        tracker = make_tracker()
        for _ in range(10):
            tracker.add_usage({"input_tokens": 200, "output_tokens": 100}, model="claude-opus-4-8")
        # hand: 10 * (200*15 + 100*75)/1e6 = 10 * (3000+7500)/1e6 = 0.105
        assert tracker.total().total_cost == pytest.approx(0.105, rel=0.05)


class TestAddUsageOverload:
    def test_dict(self) -> None:
        t = make_tracker()
        e = t.add_usage({"input_tokens": 10, "output_tokens": 5}, model="claude-opus-4-8")
        assert e.input_tokens == 10 and e.output_tokens == 5

    def test_usage_object(self) -> None:
        t = make_tracker()
        e = t.add_usage(Usage(input_tokens=10, output_tokens=5), model="claude-opus-4-8")
        assert e.input_tokens == 10

    def test_provider_response_object(self) -> None:
        class Resp:
            class _U:
                input_tokens = 20
                output_tokens = 7

            usage = _U()

        t = make_tracker()
        e = t.add_usage(Resp(), model="claude-opus-4-8")
        assert e.input_tokens == 20 and e.output_tokens == 7

    def test_unreadable_object_raises(self) -> None:
        t = make_tracker()
        with pytest.raises(TypeError, match="could not read"):
            t.add_usage(object(), model="claude-opus-4-8")


class TestUnknownModel:
    def test_counts_tokens_zero_cost_and_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        t = CostTracker()  # empty pricing table
        with caplog.at_level(logging.WARNING, logger="agentargus.observability.pricing"):
            e = t.add_usage({"input_tokens": 100, "output_tokens": 50}, model="mystery-model")
        assert e.input_tokens == 100
        assert e.cost.total_cost == 0.0
        assert any("No price registered" in r.message for r in caplog.records)


class TestCeiling:
    def test_raises_when_exceeded(self) -> None:
        t = make_tracker(ceiling_usd=0.01)
        with pytest.raises(CostCeilingExceeded):
            # 1000 in + 500 out = $0.0525 > $0.01
            t.add_usage({"input_tokens": 1000, "output_tokens": 500}, model="claude-opus-4-8")

    def test_no_ceiling_never_raises(self) -> None:
        t = make_tracker()
        t.add_usage({"input_tokens": 10_000_000, "output_tokens": 0}, model="claude-opus-4-8")
        assert t.total().total_cost > 0


class TestLedger:
    def test_per_step_entries(self) -> None:
        t = make_tracker()
        t.add_usage(
            {"input_tokens": 100, "output_tokens": 10}, model="claude-opus-4-8", step="plan"
        )
        t.add_usage(
            {"input_tokens": 200, "output_tokens": 20}, model="claude-opus-4-8", step="synthesize"
        )
        steps = [e.step for e in t.entries]
        assert steps == ["plan", "synthesize"]

    def test_table_rows(self) -> None:
        t = make_tracker()
        t.add_usage(
            {"input_tokens": 100, "output_tokens": 10}, model="claude-opus-4-8", step="plan"
        )
        rows = t.table()
        assert rows[0]["step"] == "plan"
        assert rows[0]["input_tokens"] == 100
        assert rows[0]["cost_usd"] > 0


class TestRegisterModel:
    def test_register_then_price(self) -> None:
        t = CostTracker()
        t.register_model("custom", input_per_1m=1.0, output_per_1m=2.0)
        e = t.add_usage({"input_tokens": 1_000_000, "output_tokens": 1_000_000}, model="custom")
        assert e.cost.total_cost == pytest.approx(3.0)


class TestAgentIntegration:
    def test_cost_ledger_in_run_result_metadata(self) -> None:
        tracker = make_tracker()

        def inner(x: str) -> str:
            tracker.add_usage(
                {"input_tokens": 50, "output_tokens": 25}, model="claude-opus-4-8", step="answer"
            )
            return x

        result = Agent(inner, cost=tracker).run("hi")
        assert result.cost.total_cost == pytest.approx(50 * 15 / 1e6 + 25 * 75 / 1e6)
        assert result.metadata["cost_ledger"][0]["step"] == "answer"

    def test_cost_span_emitted_when_tracer_shared(self) -> None:
        tracer = Tracer()
        tracker = CostTracker(pricing={"m": (1.0, 1.0)}, tracer=tracer)

        async def inner(x: str) -> str:
            tracker.add_usage({"input_tokens": 10, "output_tokens": 10}, model="m", step="call")
            return x

        result = Agent(inner, cost=tracker, tracer=tracer).run("x")
        span_names = {s.name for s in result.spans}
        assert "llm.call" in span_names
        assert "agent.run" in span_names
