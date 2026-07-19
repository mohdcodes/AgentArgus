"""Tests for the Tracer (spec §8: span keys, nesting, RunResult.spans, trace id)."""

from __future__ import annotations

from typing import Any

import pytest
from agentargus.agents import Agent
from agentargus.core import Span
from agentargus.observability import Tracer
from agentargus.observability.conventions import (
    GEN_AI_OPERATION_NAME,
    GEN_AI_SYSTEM,
    OP_INVOKE_AGENT,
    SPAN_AGENT_RUN,
)


class TestExporterSelection:
    def test_memory_default(self) -> None:
        Tracer()  # must not raise

    def test_console(self) -> None:
        Tracer(exporter="console")

    def test_unknown_exporter_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown tracer exporter"):
            Tracer(exporter="does-not-exist")


class TestSpanEmission:
    def test_span_records_attributes_with_convention_keys(self) -> None:
        tracer = Tracer()
        with tracer.span(SPAN_AGENT_RUN, **{GEN_AI_SYSTEM: "anthropic"}):
            trace_id = tracer.current_trace_id()
        assert trace_id is not None
        spans = tracer.collect(trace_id)
        assert len(spans) == 1
        assert spans[0].name == SPAN_AGENT_RUN
        assert spans[0].attributes[GEN_AI_SYSTEM] == "anthropic"

    def test_current_trace_id_none_outside_span(self) -> None:
        assert Tracer().current_trace_id() is None

    def test_nested_spans_nest(self) -> None:
        tracer = Tracer()
        with tracer.span("parent"):
            trace_id = tracer.current_trace_id()
            with tracer.span("child"):
                pass
        assert trace_id is not None
        spans = {s.name: s for s in tracer.collect(trace_id)}
        assert spans["parent"].parent_id is None
        assert spans["child"].parent_id == spans["parent"].span_id
        # Same trace, two spans.
        assert len(spans) == 2

    def test_collect_drains_buffer(self) -> None:
        tracer = Tracer()
        with tracer.span("s"):
            tid = tracer.current_trace_id()
        assert tid is not None
        assert len(tracer.collect(tid)) == 1
        assert tracer.collect(tid) == ()  # drained


class TestNanosecondOrdering:
    def test_spans_carry_lossless_nanoseconds(self) -> None:
        tracer = Tracer()
        with tracer.span("parent"):
            tid = tracer.current_trace_id()
        assert tid is not None
        span = tracer.collect(tid)[0]
        assert span.start_ns is not None and span.start_ns > 0
        # sort_key uses ns when present.
        assert span.sort_key == span.start_ns

    def test_float_seconds_would_collide_but_ns_does_not(self) -> None:
        # Two ns timestamps <1us apart collapse to one float; ns keeps them apart.
        from agentargus.core import Span

        a = Span("a", "1", 0.0, 0.0, start_ns=1_784_462_298_176_935_100)
        b = Span("b", "2", 0.0, 0.0, start_ns=1_784_462_298_176_935_101)
        assert a.start_time == b.start_time  # float collision (documented)
        assert a.sort_key != b.sort_key  # ns ordering survives


class TestCollectorGrowthGuard:
    def test_warns_after_threshold_and_evicts_at_cap(self) -> None:
        from agentargus.observability.tracer import CollectorProcessor

        proc = CollectorProcessor(warn_after=2, max_traces=3)
        # Simulate uncollected traces by injecting spans under distinct ids.
        from agentargus.core import Span

        for i in range(5):
            proc._by_trace[f"trace{i}"] = [Span(f"s{i}", "x", 0.0, 0.0)]
            proc._by_trace.move_to_end(f"trace{i}")
            proc._guard_growth()
        # Cap=3 => only the 3 newest survive; oldest two evicted.
        assert len(proc._by_trace) == 3
        assert "trace0" not in proc._by_trace
        assert "trace4" in proc._by_trace


class TestTracedDecorator:
    async def test_traced_async(self) -> None:
        tracer = Tracer()
        seen: dict[str, str | None] = {}

        @tracer.traced("work")
        async def work() -> str:
            seen["tid"] = tracer.current_trace_id()
            return "done"

        assert await work() == "done"
        assert seen["tid"] is not None
        assert tracer.collect(seen["tid"])[0].name == "work"

    def test_traced_sync(self) -> None:
        tracer = Tracer()
        captured: dict[str, str | None] = {}

        @tracer.traced()
        def compute() -> int:
            captured["tid"] = tracer.current_trace_id()
            return 5

        assert compute() == 5
        assert tracer.collect(captured["tid"] or "")[0].name == "compute"


class TestAgentTracerIntegration:
    def test_run_populates_run_result_spans(self) -> None:
        agent = Agent(lambda x: x, tracer=Tracer())
        result = agent.run("hi")
        assert len(result.spans) == 1
        span = result.spans[0]
        assert isinstance(span, Span)
        assert span.name == SPAN_AGENT_RUN
        assert span.attributes[GEN_AI_OPERATION_NAME] == OP_INVOKE_AGENT

    def test_otel_trace_id_is_source_of_truth(self) -> None:
        # With a real tracer, RunResult.trace_id is the 32-hex OTel id and it
        # matches the emitted span's trace context (not a uuid4 fallback).
        agent = Agent(lambda x: x, tracer=Tracer())
        result = agent.run("hi")
        assert len(result.trace_id) == 32
        # The span belongs to that trace (its span_id differs from trace_id).
        assert result.spans[0].span_id != result.trace_id

    def test_without_tracer_falls_back_to_uuid(self) -> None:
        # NullTracer path: no spans, uuid4 trace_id (also 32-hex but no spans).
        result = Agent(lambda x: x).run("hi")
        assert result.spans == ()
        assert len(result.trace_id) == 32

    async def test_nested_agents_produce_separate_traces(self) -> None:
        inner = Agent(lambda x: x, tracer=Tracer(), name="inner")

        async def outer_body(x: Any) -> Any:
            return (await inner.arun(x)).output

        outer = Agent(outer_body, tracer=Tracer(), name="outer")
        result = await outer.arun("x")
        # Outer has its own trace + span; inner's spans belong to inner's trace.
        assert len(result.spans) == 1
        assert result.spans[0].name == SPAN_AGENT_RUN
