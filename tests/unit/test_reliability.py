"""Tests for the reliability layer (spec §8)."""

from __future__ import annotations

import threading
from typing import Any

import pytest
from agentargus import (
    Agent,
    CircuitBreaker,
    CircuitOpenError,
    FallbackChain,
    ReliabilityPolicy,
    RetryWithBackoff,
    TransientError,
)
from agentargus.reliability.base import RetryContext
from agentargus.reliability.dead_letter import (
    DeadLetterQueue,
    InMemoryDeadLetterSink,
    JsonlDeadLetterSink,
)


async def _nosleep(_: float) -> None:
    return None


def ctx() -> RetryContext:
    return RetryContext(step="test")


class TestRetry:
    async def test_succeeds_on_nth_attempt(self) -> None:
        calls = {"n": 0}

        async def flaky(_: Any) -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise TransientError("boom")
            return "ok"

        retry = RetryWithBackoff(max_attempts=3, sleep=_nosleep)
        c = ctx()
        assert await retry.execute(flaky, "x", c) == "ok"
        assert calls["n"] == 3
        # Two failed-but-recovered attempts recorded.
        assert len(c.errors) == 2
        assert all(e.recovered for e in c.errors)

    async def test_exhausts_and_reraises(self) -> None:
        async def always(_: Any) -> str:
            raise TransientError("nope")

        retry = RetryWithBackoff(max_attempts=2, sleep=_nosleep)
        c = ctx()
        with pytest.raises(TransientError):
            await retry.execute(always, "x", c)
        assert c.errors[-1].recovered is False

    async def test_non_retryable_fails_fast(self) -> None:
        calls = {"n": 0}

        async def bug(_: Any) -> str:
            calls["n"] += 1
            raise ValueError("programming error")

        retry = RetryWithBackoff(max_attempts=5, sleep=_nosleep)
        with pytest.raises(ValueError):
            await retry.execute(bug, "x", ctx())
        assert calls["n"] == 1  # not retried


class TestFallback:
    async def test_moves_to_next_on_failure(self) -> None:
        async def primary(_: Any) -> str:
            raise RuntimeError("primary down")

        chain = FallbackChain([lambda _: "from-fallback"])
        assert await chain.execute(primary, "x", ctx()) == "from-fallback"

    async def test_exhaustion_reraises_last(self) -> None:
        async def primary(_: Any) -> str:
            raise RuntimeError("p")

        def alt(_: Any) -> str:
            raise RuntimeError("a")

        chain = FallbackChain([alt])
        with pytest.raises(RuntimeError):
            await chain.execute(primary, "x", ctx())


class TestCircuitBreaker:
    def test_opens_after_threshold(self) -> None:
        cb = CircuitBreaker(failure_threshold=3, cooldown=100.0, clock=lambda: 0.0)
        assert cb.state == "CLOSED"
        for _ in range(3):
            cb.record_failure()
        assert cb.state == "OPEN"
        assert cb.allow() is False

    def test_half_opens_after_cooldown_then_closes(self) -> None:
        now = {"t": 0.0}
        cb = CircuitBreaker(failure_threshold=1, cooldown=10.0, clock=lambda: now["t"])
        cb.record_failure()
        assert cb.state == "OPEN"
        now["t"] = 11.0
        assert cb.allow() is True  # transitions to HALF_OPEN
        assert cb.state == "HALF_OPEN"
        cb.record_success()
        assert cb.state == "CLOSED"

    def test_half_open_trial_failure_reopens(self) -> None:
        now = {"t": 0.0}
        cb = CircuitBreaker(failure_threshold=1, cooldown=10.0, clock=lambda: now["t"])
        cb.record_failure()
        now["t"] = 11.0
        cb.allow()  # -> HALF_OPEN
        cb.record_failure()  # trial fails
        assert cb.state == "OPEN"

    async def test_execute_fails_fast_when_open(self) -> None:
        cb = CircuitBreaker(failure_threshold=1, cooldown=100.0, clock=lambda: 0.0)
        cb.record_failure()  # now OPEN

        async def fn(_: Any) -> str:
            raise AssertionError("should not be called when OPEN")

        with pytest.raises(CircuitOpenError):
            await cb.execute(fn, "x", ctx())

    def test_thread_safe_record_failure(self) -> None:
        # Hammer record_failure from many threads; state must stay consistent.
        cb = CircuitBreaker(failure_threshold=1000, cooldown=100.0, clock=lambda: 0.0)

        def worker() -> None:
            for _ in range(1000):
                cb.record_failure()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 8 * 1000 = 8000 failures >> threshold; must be OPEN, no corruption/crash.
        assert cb.state == "OPEN"


class TestDeadLetter:
    def test_inmemory_sink_captures(self) -> None:
        sink = InMemoryDeadLetterSink()
        dlq = DeadLetterQueue(sink)
        dlq.put(input={"q": "x"}, error=RuntimeError("fail"), step="synthesis")
        assert sink.records[0]["error_type"] == "RuntimeError"
        assert sink.records[0]["step"] == "synthesis"

    def test_jsonl_sink_writes(self, tmp_path: Any) -> None:
        import json

        path = tmp_path / "dlq.jsonl"
        dlq = DeadLetterQueue(JsonlDeadLetterSink(path))
        dlq.put(input="bad", error=ValueError("x"), step="plan")
        line = path.read_text(encoding="utf-8").strip()
        assert json.loads(line)["error_type"] == "ValueError"


class TestReliabilityPolicy:
    async def test_retry_then_fallback_compose(self) -> None:
        # Primary always fails transiently; retry exhausts; fallback saves it.
        async def primary(_: Any) -> str:
            raise TransientError("primary flaky")

        policy = ReliabilityPolicy(
            retry=RetryWithBackoff(max_attempts=2, sleep=_nosleep),
            fallbacks=[lambda _: "rescued"],
        )
        assert await policy(primary, "x") == "rescued"
        assert policy.last_errors  # retries + primary fallback failure recorded

    async def test_dlq_captures_terminal_failure(self) -> None:
        sink = InMemoryDeadLetterSink()

        async def doomed(_: Any) -> str:
            raise TransientError("always")

        policy = ReliabilityPolicy(
            retry=RetryWithBackoff(max_attempts=1, sleep=_nosleep),
            dead_letter=sink,
        )
        with pytest.raises(TransientError):
            await policy(doomed, "the-input")
        assert sink.records[0]["input"] == "the-input"


class TestAgentIntegration:
    def test_agent_recovers_from_injected_failure(self) -> None:
        # The gate: Agent(reliability=...) recovers, and the recovery shows up
        # in RunResult.errors with recovered=True.
        state = {"n": 0}

        def flaky(x: str) -> str:
            state["n"] += 1
            if state["n"] < 2:
                raise TransientError("transient")
            return f"ok:{x}"

        policy = ReliabilityPolicy(retry=RetryWithBackoff(max_attempts=3, sleep=_nosleep))
        result = Agent(flaky, reliability=policy).run("hi")
        assert result.output == "ok:hi"
        assert len(result.errors) == 1
        assert result.errors[0].recovered is True
        assert result.errors[0].error_type == "TransientError"

    def test_agent_without_reliability_has_no_errors(self) -> None:
        result = Agent(lambda x: x).run("x")
        assert result.errors == ()
