"""Tests for orchestration patterns (spec §8: routing, handoff, guards, resume)."""

from __future__ import annotations

from typing import Any

import pytest
from agentargus import Agent, Handoff, SupervisorAgent
from agentargus._internal.exceptions import OrchestrationError
from agentargus.agents.checkpoint_store import InMemoryCheckpointer
from agentargus.core import RunResult


class FakeRouter:
    """Routes to a fixed worker name."""

    def __init__(self, target: str) -> None:
        self._target = target

    def route(self, input: Any, workers: dict[str, Any]) -> str:
        return self._target


def worker(reply: str) -> Agent:
    return Agent(lambda x: reply, name=reply)


class TestRouting:
    def test_routes_to_chosen_worker(self) -> None:
        sup = SupervisorAgent(
            {"a": worker("from-a"), "b": worker("from-b"), "c": worker("from-c")},
            router=FakeRouter("b"),
        )
        result = sup.run("q")
        assert result.output == "from-b"

    def test_router_unknown_worker_raises(self) -> None:
        sup = SupervisorAgent({"a": worker("x")}, router=FakeRouter("nonexistent"))
        with pytest.raises(OrchestrationError, match="unknown worker"):
            sup.run("q")


class TestHandoffChain:
    def test_worker_hands_off_to_next(self) -> None:
        # retrieval hands off to synthesis; synthesis returns final answer.
        def retrieval(x: str) -> Handoff:
            return Handoff(target="synthesis", input="docs", context={"found": 3})

        workers = {
            "retrieval": Agent(retrieval, name="retrieval"),
            "synthesis": Agent(lambda x: f"answer from {x}", name="synthesis"),
        }
        sup = SupervisorAgent(workers, router=FakeRouter("retrieval"))
        result = sup.run("question")
        assert result.output == "answer from docs"

    def test_max_steps_exceeded_raises(self) -> None:
        # Two workers ping-pong forever.
        def a(x: Any) -> Handoff:
            return Handoff(target="b", input=x)

        def b(x: Any) -> Handoff:
            return Handoff(target="a", input=x)

        workers = {"a": Agent(a, name="a"), "b": Agent(b, name="b")}
        sup = SupervisorAgent(workers, router=FakeRouter("a"), max_steps=5)
        with pytest.raises(OrchestrationError, match="max_steps"):
            sup.run("q")


class TestValidation:
    def test_empty_workers_raises(self) -> None:
        with pytest.raises(OrchestrationError, match="at least one worker"):
            SupervisorAgent({}, router=FakeRouter("x"))

    def test_bad_max_steps_raises(self) -> None:
        with pytest.raises(OrchestrationError, match="max_steps"):
            SupervisorAgent({"a": worker("x")}, router=FakeRouter("a"), max_steps=0)


class TestContextGuard:
    def test_oversized_context_raises(self) -> None:
        big = {"blob": "x" * 2_000_000}

        def a(x: Any) -> Handoff:
            return Handoff(target="b", input=x, context=big)

        workers = {"a": Agent(a, name="a"), "b": worker("done")}
        sup = SupervisorAgent(workers, router=FakeRouter("a"))
        with pytest.raises(OrchestrationError, match="context"):
            sup.run("q")


class TestPartialFailure:
    def test_worker_failure_returns_partial_result_not_crash(self) -> None:
        def boom(x: Any) -> str:
            raise RuntimeError("worker down")

        sup = SupervisorAgent({"a": Agent(boom, name="a")}, router=FakeRouter("a"))
        result = sup.run("q")
        assert isinstance(result, RunResult)
        assert result.metadata["failed"] is True
        assert result.output is None
        assert result.errors[0].error_type == "RuntimeError"
        assert result.errors[0].recovered is False


class TestCheckpointResume:
    def test_three_agent_run_persists_and_resumes(self) -> None:
        # A -> B -> C chain, checkpointed. A fresh supervisor with the same
        # run_id and checkpointer resumes without re-running completed workers.
        calls: list[str] = []

        def a(x: Any) -> Handoff:
            calls.append("a")
            return Handoff(target="b", input="to-b")

        def b(x: Any) -> Handoff:
            calls.append("b")
            return Handoff(target="c", input="to-c")

        def c(x: Any) -> str:
            calls.append("c")
            return "final"

        workers = {
            "a": Agent(a, name="a"),
            "b": Agent(b, name="b"),
            "c": Agent(c, name="c"),
        }
        cp = InMemoryCheckpointer()
        run_id = "run-123"

        sup1 = SupervisorAgent(workers, router=FakeRouter("a"), checkpointer=cp, run_id=run_id)
        result1 = sup1.run("q")
        assert result1.output == "final"
        assert calls == ["a", "b", "c"]

        # New supervisor, same run_id + checkpointer => resume, workers NOT re-run.
        calls.clear()
        sup2 = SupervisorAgent(workers, router=FakeRouter("a"), checkpointer=cp, run_id=run_id)
        result2 = sup2.run("q")
        assert result2.output == "final"
        assert calls == []  # everything replayed from checkpoints


class TestComposes:
    def test_supervisor_wrapped_by_agent(self) -> None:
        # SupervisorAgent is-a BaseAgent, so Agent can wrap it.
        sup = SupervisorAgent({"a": worker("hi")}, router=FakeRouter("a"))
        outer = Agent(sup, name="outer")
        assert outer.run("q").output == "hi"
