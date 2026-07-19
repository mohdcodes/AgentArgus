"""Tests for human-in-the-loop checkpoints (spec §8)."""

from __future__ import annotations

import pytest
from agentargus import (
    Agent,
    AutoApproveBackend,
    AutoRejectBackend,
    CallbackApprovalBackend,
    Checkpoint,
    Decision,
)
from agentargus._internal.exceptions import CheckpointRejected
from agentargus.agents.checkpoint_store import InMemoryCheckpointer
from agentargus.hitl.checkpoint import ConsoleApprovalBackend


class TestDecisionFlow:
    async def test_approval_returns_decision(self) -> None:
        cp = Checkpoint(AutoApproveBackend(), name="spend")
        decision = await cp.require_approval({"cost": 2.50})
        assert decision.approved is True
        assert decision.reason == "auto-approved"

    async def test_rejection_raises_checkpoint_rejected(self) -> None:
        cp = Checkpoint(AutoRejectBackend("too expensive"), name="spend")
        with pytest.raises(CheckpointRejected, match="too expensive"):
            await cp.require_approval({"cost": 999})

    async def test_edited_input_honoured(self) -> None:
        async def reviewer(ctx: dict) -> Decision:
            return Decision(approved=True, edited_input="cheaper query")

        cp = Checkpoint(CallbackApprovalBackend(reviewer))
        decision = await cp({"query": "expensive query"})
        assert decision.edited_input == "cheaper query"


class TestBackends:
    async def test_sync_callback_auto_wrapped(self) -> None:
        def reviewer(ctx: dict) -> Decision:
            return Decision(approved=True, reason="sync ok")

        decision = await Checkpoint(CallbackApprovalBackend(reviewer)).require_approval({})
        assert decision.reason == "sync ok"

    async def test_console_non_tty_fails_safe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # No TTY (the test env) => reject, don't hang on stdin.
        decision = await ConsoleApprovalBackend().decide({"x": 1})
        assert decision.approved is False
        assert "TTY" in (decision.reason or "")


class TestAgentIntegration:
    def test_approval_lets_run_proceed(self) -> None:
        async def agent_fn(q: str) -> str:
            cp = Checkpoint(AutoApproveBackend(), name="crawl")
            await cp.require_approval({"action": "deep crawl", "cost": 2.5})
            return f"crawled: {q}"

        result = Agent(agent_fn).run("tesla")
        assert result.output == "crawled: tesla"
        assert result.metadata.get("failed") is not True

    def test_rejection_is_controlled_failure_in_errors(self) -> None:
        # THE GATE: a rejected checkpoint => controlled failure recorded, no crash.
        async def agent_fn(q: str) -> str:
            cp = Checkpoint(AutoRejectBackend("policy: no crawl"), name="crawl")
            await cp.require_approval({"action": "deep crawl"})
            return "should not reach here"

        result = Agent(agent_fn).run("tesla")
        assert result.output is None
        assert result.metadata["failed"] is True
        assert result.errors[0].error_type == "CheckpointRejected"
        assert result.errors[0].recovered is False
        assert "no crawl" in result.errors[0].message

    def test_approval_recorded_as_step(self) -> None:
        async def agent_fn(q: str) -> str:
            await Checkpoint(AutoApproveBackend(), name="gate").require_approval({})
            return "done"

        result = Agent(agent_fn).run("q")
        approval_steps = [s for s in result.steps if s.kind == "approval"]
        assert approval_steps
        assert approval_steps[0].metadata["approved"] is True


class TestResume:
    async def test_prior_approval_replayed_not_reprompted(self) -> None:
        cp_store = InMemoryCheckpointer()
        calls = {"n": 0}

        def reviewer(ctx: dict) -> Decision:
            calls["n"] += 1
            return Decision(approved=True, reason="approved once", edited_input="edited")

        # First run: prompts once, persists the approval.
        cp1 = Checkpoint(
            CallbackApprovalBackend(reviewer),
            name="gate",
            checkpointer=cp_store,
            run_id="run-1",
        )
        await cp1.require_approval({"x": 1})
        assert calls["n"] == 1

        # Second checkpoint, same run_id + store => replay, no re-prompt.
        cp2 = Checkpoint(
            CallbackApprovalBackend(reviewer),
            name="gate",
            checkpointer=cp_store,
            run_id="run-1",
        )
        decision = await cp2.require_approval({"x": 1})
        assert calls["n"] == 1  # NOT re-prompted
        assert decision.approved is True
        assert decision.edited_input == "edited"
