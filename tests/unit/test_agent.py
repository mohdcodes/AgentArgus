"""Tests for BaseAgent + Agent facade (spec §8: wrap, run/arun, trace_id)."""

from __future__ import annotations

import asyncio
import io
import json
from typing import Any

import pytest
from agentargus.agents import Agent, BaseAgent
from agentargus.core import RunResult
from agentargus.logging import configure_logging, get_logger, get_trace_id


class TestWrapCallable:
    def test_wraps_sync_callable_and_run_returns_run_result(self) -> None:
        agent = Agent(lambda x: f"echo:{x}")
        result = agent.run("hi")
        assert isinstance(result, RunResult)
        assert result.output == "echo:hi"

    def test_wraps_async_callable(self) -> None:
        async def inner(x: str) -> str:
            await asyncio.sleep(0)
            return f"async:{x}"

        result = Agent(inner).run("hi")
        assert result.output == "async:hi"

    async def test_arun_path(self) -> None:
        result = await Agent(lambda x: x * 2).arun(21)
        assert result.output == 42

    def test_non_callable_non_agent_raises(self) -> None:
        with pytest.raises(TypeError, match="BaseAgent or a callable"):
            Agent(42)  # an int is neither


class TestWrapBaseAgent:
    def test_wraps_inner_base_agent(self) -> None:
        class Inner(BaseAgent):
            async def arun(self, input: Any) -> RunResult:
                return RunResult(output=f"inner:{input}", trace_id="inner-tid")

        outer = Agent(Inner())
        result = outer.run("x")
        # The facade surfaces the inner agent's output, wrapped in its own result.
        assert result.output == "inner:x"
        assert result.trace_id != "inner-tid"  # outer generates its own trace_id

    def test_base_agent_overload_dispatches_not_overwritten(self) -> None:
        # Regression guard: the BaseAgent @overload must survive alongside the
        # object catch-all overload (a plain method would overwrite it).
        calls: list[str] = []

        class Inner(BaseAgent):
            async def arun(self, input: Any) -> RunResult:
                calls.append("inner.arun")
                return RunResult(output="ok", trace_id="t")

        Agent(Inner()).run("x")
        assert calls == ["inner.arun"]  # proves the BaseAgent branch ran


class TestRunResultAssembly:
    def test_trace_id_is_populated(self) -> None:
        result = Agent(lambda x: x).run("x")
        assert result.trace_id
        assert len(result.trace_id) == 32  # uuid4 hex

    def test_metadata_carries_agent_name(self) -> None:
        def my_agent(x: str) -> str:
            return x

        result = Agent(my_agent).run("x")
        assert result.metadata["agent_name"] == "my_agent"

    def test_cost_is_zero_with_null_tracker(self) -> None:
        result = Agent(lambda x: x).run("x")
        assert result.cost.total_cost == 0.0


class TestTraceCorrelation:
    def test_trace_id_bound_during_run_and_cleared_after(self) -> None:
        seen: dict[str, str | None] = {}

        def inner(x: str) -> str:
            seen["during"] = get_trace_id()
            return x

        # Note: sync callable runs in a worker thread (to_thread), so it will
        # NOT see the contextvar unless propagated. Assert the async path here.
        async def ainner(x: str) -> str:
            seen["during"] = get_trace_id()
            return x

        result = Agent(ainner).run("x")
        assert seen["during"] == result.trace_id
        assert get_trace_id() is None  # cleared after run

    def test_nested_agents_get_distinct_nested_trace_ids(self) -> None:
        # Trace model: each Agent.arun() sets its OWN trace_id. When one Agent
        # delegates to another Agent, the inner run shadows the outer's id for
        # its duration, then the outer's is restored (contextvar token nesting).
        # The two ids NEST — they do not compete or merge.
        #
        # NOTE: this holds specifically because the inner is a real ``Agent``
        # (which calls set_trace_id). A *bare* BaseAgent that never sets an id
        # would simply inherit the outer's context — that is correct behaviour,
        # not a bug: only Agent participates in the trace-id scheme.
        seen: dict[str, str | None] = {}
        inner_agent = Agent(lambda x: x, name="inner")

        async def delegating(x: Any) -> Any:
            seen["outer_during"] = get_trace_id()
            inner_result = await inner_agent.arun(x)
            seen["inner_own"] = inner_result.trace_id
            seen["outer_after_inner"] = get_trace_id()
            return inner_result.output

        outer_result = Agent(delegating, name="outer").run("x")

        # The inner Agent produced its own, distinct trace_id.
        assert seen["inner_own"] != seen["outer_during"]
        # After the inner returned, the outer's trace_id was restored (nesting).
        assert seen["outer_after_inner"] == seen["outer_during"]
        assert seen["outer_during"] == outer_result.trace_id
        # And the context is fully cleared once everything unwinds.
        assert get_trace_id() is None

    def test_trace_id_in_logs(self) -> None:
        stream = io.StringIO()
        configure_logging(level="DEBUG", color=False, json_format=True, stream=stream)

        async def ainner(x: str) -> str:
            get_logger("test").info("inside run")
            return x

        result = Agent(ainner).run("x")
        lines = [json.loads(ln) for ln in stream.getvalue().strip().splitlines() if ln]
        inside = [ln for ln in lines if ln["message"] == "inside run"]
        assert inside and inside[0]["trace_id"] == result.trace_id

    def test_no_uuid4_window_with_tracer(self) -> None:
        # HARD_QUESTIONS Module 2 #3: with a real Tracer, EVERY agent.run log
        # line carries the OTel trace id — no line shows a throwaway uuid4.
        from agentargus.observability import Tracer

        stream = io.StringIO()
        configure_logging(level="DEBUG", color=False, json_format=True, stream=stream)
        result = Agent(lambda x: x, tracer=Tracer(), name="w").run("x")
        run_lines = [
            json.loads(ln)
            for ln in stream.getvalue().strip().splitlines()
            if ln and "agent.run" in ln
        ]
        assert run_lines  # sanity
        assert all(ln["trace_id"] == result.trace_id for ln in run_lines)


class TestSyncLoopGuard:
    async def test_run_inside_running_loop_raises(self) -> None:
        agent = Agent(lambda x: x)
        with pytest.raises(RuntimeError, match="within a running event loop"):
            agent.run("x")  # we are already inside the asyncio test loop
