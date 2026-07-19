"""Human-in-the-loop checkpoints (spec §6.7).

A ``Checkpoint`` pauses a run to obtain a human ``Decision`` via a pluggable
``ApprovalBackend``. Approval lets the run proceed (optionally with a human-edited
input); rejection raises ``CheckpointRejected`` — a *controlled* failure the
surrounding Agent/Supervisor records as an ``ErrorRecord``. Pending approvals can
persist via the Module 8 ``Checkpointer`` so a run resumes across a restart.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from agentargus._internal.exceptions import CheckpointRejected
from agentargus.agents.recorder import record_step
from agentargus.logging import get_logger

if TYPE_CHECKING:
    from agentargus.agents.checkpoint_store import Checkpointer

__all__ = [
    "Decision",
    "ApprovalBackend",
    "CallbackApprovalBackend",
    "ConsoleApprovalBackend",
    "AutoApproveBackend",
    "AutoRejectBackend",
    "Checkpoint",
]

_logger = get_logger("hitl.checkpoint")


@dataclass(frozen=True)
class Decision:
    """A human's approval decision.

    ``edited_input`` lets a reviewer redirect what the agent does next
    (approve-with-modification); ``reason`` is the audit trail.
    """

    approved: bool
    reason: str | None = None
    edited_input: Any = None


@runtime_checkable
class ApprovalBackend(Protocol):
    """Obtains a ``Decision`` for a checkpoint (async-first)."""

    async def decide(self, context: Mapping[str, Any]) -> Decision: ...


class CallbackApprovalBackend:
    """Wraps a user callable (sync or async) as an ApprovalBackend.

    A coroutine function is awaited; a plain function runs via ``to_thread`` so a
    blocking callback never stalls the event loop.
    """

    def __init__(self, fn: Callable[[Mapping[str, Any]], Decision | Awaitable[Decision]]) -> None:
        self._fn = fn

    async def decide(self, context: Mapping[str, Any]) -> Decision:
        if inspect.iscoroutinefunction(self._fn):
            return await self._fn(context)  # type: ignore[no-any-return]
        return await asyncio.to_thread(self._fn, context)  # type: ignore[arg-type]


class ConsoleApprovalBackend:
    """Prompts on stdin for local/dev use.

    In a non-interactive context (no TTY, e.g. CI) it does NOT hang on stdin — it
    logs and defaults to reject, so an unattended run fails safe rather than
    blocking forever.
    """

    async def decide(self, context: Mapping[str, Any]) -> Decision:
        if not sys.stdin or not sys.stdin.isatty():
            _logger.warning("ConsoleApprovalBackend: no TTY; defaulting to REJECT (fail-safe).")
            return Decision(approved=False, reason="no interactive TTY for approval")
        prompt = f"\nAPPROVAL NEEDED: {dict(context)}\nApprove? [y/N]: "
        answer = await asyncio.to_thread(input, prompt)
        approved = answer.strip().lower() in {"y", "yes"}
        return Decision(approved=approved, reason="console decision")


class AutoApproveBackend:
    """Always approves (tests / trusted policy)."""

    async def decide(self, context: Mapping[str, Any]) -> Decision:
        return Decision(approved=True, reason="auto-approved")


class AutoRejectBackend:
    """Always rejects (tests / policy)."""

    def __init__(self, reason: str = "auto-rejected") -> None:
        self._reason = reason

    async def decide(self, context: Mapping[str, Any]) -> Decision:
        return Decision(approved=False, reason=self._reason)


class Checkpoint:
    """A pause point that requires human approval before a run continues."""

    def __init__(
        self,
        backend: ApprovalBackend,
        *,
        name: str = "checkpoint",
        checkpointer: Checkpointer | None = None,
        run_id: str | None = None,
    ) -> None:
        self._backend = backend
        self._name = name
        self._checkpointer = checkpointer
        self._run_id = run_id

    async def require_approval(self, context: Mapping[str, Any]) -> Decision:
        """Obtain approval; return the Decision or raise ``CheckpointRejected``.

        On resume, a previously-approved decision for this ``(run_id, name)`` is
        replayed instead of re-prompting.
        """
        replayed = self._replay_if_approved()
        if replayed is not None:
            _logger.info("checkpoint %s: replaying prior approval", self._name)
            return replayed

        self._persist("pending", context, approved=None)
        decision = await self._backend.decide(context)
        record_step(
            "approval",
            f"checkpoint {self._name}: {'approved' if decision.approved else 'rejected'}",
            checkpoint=self._name,
            approved=decision.approved,
            reason=decision.reason,
        )
        if not decision.approved:
            self._persist("rejected", context, approved=False)
            raise CheckpointRejected(self._name, decision.reason)
        self._persist("approved", context, approved=True, decision=decision)
        return decision

    async def __call__(self, context: Mapping[str, Any]) -> Decision:
        return await self.require_approval(context)

    # ------------------------------------------------------------------ #
    def _step_key(self) -> int:
        # A stable-ish step slot for this checkpoint's persistence. Uses a hash
        # of the name so multiple named checkpoints don't collide.
        return abs(hash(self._name)) % 1_000_000

    def _replay_if_approved(self) -> Decision | None:
        if self._checkpointer is None or self._run_id is None:
            return None
        for row in self._checkpointer.load_steps(self._run_id):
            if row["worker"] == f"checkpoint:{self._name}" and row["status"] == "completed":
                out = row.get("output") or {}
                return Decision(
                    approved=True,
                    reason=out.get("reason"),
                    edited_input=out.get("edited_input"),
                )
        return None

    def _persist(
        self,
        state: str,
        context: Mapping[str, Any],
        *,
        approved: bool | None,
        decision: Decision | None = None,
    ) -> None:
        if self._checkpointer is None or self._run_id is None:
            return
        from agentargus.agents.checkpoint_store import (
            STATUS_COMPLETED,
            STATUS_FAILED,
            STATUS_RUNNING,
        )

        status = {
            "pending": STATUS_RUNNING,
            "approved": STATUS_COMPLETED,
            "rejected": STATUS_FAILED,
        }[state]
        output = None
        if decision is not None:
            output = {"reason": decision.reason, "edited_input": decision.edited_input}
        self._checkpointer.save_step(
            self._run_id,
            self._step_key(),
            f"checkpoint:{self._name}",
            dict(context),
            output,
            status,
        )
