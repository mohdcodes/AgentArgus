"""Shared pytest fixtures for AgentArgus tests."""

from __future__ import annotations

import pytest
from agentargus.core import CostBreakdown, ErrorRecord, RunResult, Span, Step, ToolCall


@pytest.fixture
def sample_run_result() -> RunResult:
    """A representative RunResult populated across every field."""
    return RunResult(
        output="the answer is 42",
        trace_id="trace-abc-123",
        spans=[
            Span(
                name="agent.run",
                span_id="s1",
                start_time=0.0,
                end_time=1.5,
                attributes={"gen_ai.system": "anthropic"},
            )
        ],
        cost=CostBreakdown(input_tokens=100, output_tokens=50, input_cost=0.001, output_cost=0.002),
        tool_calls=[
            ToolCall(name="search", args={"q": "meaning"}, result="42", success=True, latency=0.3)
        ],
        steps=[Step(index=0, kind="reason", content="think about it")],
        errors=[ErrorRecord(error_type="TimeoutError", message="slow", recovered=True, attempt=1)],
        metadata={"user": "test"},
    )
