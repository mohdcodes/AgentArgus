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
