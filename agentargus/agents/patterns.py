"""Orchestration patterns (spec §6.6): SupervisorAgent, Handoff, Router.

``SupervisorAgent`` is-a ``BaseAgent`` that routes an input to one of its
``workers`` (polymorphic over ``BaseAgent``), following a handoff chain until a
worker returns a non-``Handoff`` result. Production-hardened: per-hop spans +
structured routing logs, construction/route validation, ``max_steps`` and
context-size guards, per-step checkpointing with graceful partial-failure.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from agentargus._internal.exceptions import OrchestrationError
from agentargus.agents.base import BaseAgent
from agentargus.agents.checkpoint_store import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    Checkpointer,
)
from agentargus.agents.recorder import record_step
from agentargus.core import ErrorRecord, RunResult
from agentargus.logging import get_logger

if TYPE_CHECKING:
    from agentargus.config import Judge

__all__ = ["Handoff", "Router", "LLMRouter", "SupervisorAgent"]

_logger = get_logger("agents.patterns")

# Guard: cap accumulated handoff context to prevent unbounded memory growth.
_MAX_CONTEXT_BYTES = 1_000_000  # ~1 MB serialized


@dataclass(frozen=True)
class Handoff:
    """A worker's request to transfer control to another worker.

    Returning a ``Handoff`` (rather than a plain value) tells the supervisor to
    continue the chain: run ``target`` with ``input``, threading ``context``
    (accumulated state) forward.
    """

    target: str
    input: Any
    context: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class Router(Protocol):
    """Chooses which worker handles an input. Returns a worker *name*."""

    def route(self, input: Any, workers: Mapping[str, BaseAgent]) -> str: ...


class LLMRouter:
    """Default router: an LLM judge picks the best worker by name/description."""

    def __init__(self, judge: Judge, descriptions: Mapping[str, str] | None = None) -> None:
        self._judge = judge
        self._descriptions = dict(descriptions or {})

    def route(self, input: Any, workers: Mapping[str, BaseAgent]) -> str:
        catalog = "\n".join(f"- {name}: {self._descriptions.get(name, name)}" for name in workers)
        prompt = (
            "Choose the single best worker to handle the INPUT. Reply with ONLY "
            f"the worker name.\n\nWORKERS:\n{catalog}\n\nINPUT:\n{input}"
        )
        choice = self._judge.complete(prompt).strip()
        # Tolerant: if the judge wraps the name in prose, find a known name in it.
        if choice not in workers:
            for name in workers:
                if name in choice:
                    return name
        return choice


class SupervisorAgent(BaseAgent):
    """Routes to workers and follows a handoff chain (is-a BaseAgent)."""

    def __init__(
        self,
        workers: Mapping[str, BaseAgent],
        *,
        router: Router,
        max_steps: int = 10,
        checkpointer: Checkpointer | None = None,
        run_id: str | None = None,
        tracer: Any = None,
        dead_letter: Any = None,
        name: str = "supervisor",
    ) -> None:
        # --- fail-fast validation at construction ---
        if not workers:
            raise OrchestrationError("SupervisorAgent needs at least one worker.")
        # Mapping keys are unique, but guard against a caller passing pairs.
        self._workers: dict[str, BaseAgent] = dict(workers)
        if len(self._workers) != len(workers):  # pragma: no cover - dict dedups
            raise OrchestrationError("Duplicate worker names are not allowed.")
        if max_steps < 1:
            raise OrchestrationError("max_steps must be >= 1.")
        self._router = router
        self._max_steps = max_steps
        self._checkpointer = checkpointer
        self._run_id = run_id or uuid.uuid4().hex
        self._tracer = tracer
        self._dlq = dead_letter
        self._name = name

    @property
    def run_id(self) -> str:
        return self._run_id

    async def arun(self, input: Any) -> RunResult:
        errors: list[ErrorRecord] = []
        first = self._route(input)
        current: Handoff | Any = Handoff(target=first, input=input)
        output: Any = None
        step = 0
        last_completed = (
            self._checkpointer.last_completed_step(self._run_id)
            if self._checkpointer is not None
            else -1
        )

        while step < self._max_steps:
            assert isinstance(current, Handoff)
            worker_name = current.target
            if worker_name not in self._workers:
                raise OrchestrationError(
                    f"Router/handoff targeted unknown worker {worker_name!r}; "
                    f"known workers: {sorted(self._workers)}."
                )
            self._check_context_size(current.context)

            # Resume: replay a step already completed in a prior run.
            if step <= last_completed and self._checkpointer is not None:
                cached = self._checkpointer.completed_output(self._run_id, step)
                _logger.info("resume: replaying completed step %d (%s)", step, worker_name)
                output = cached
                current = output
                step += 1
                if not isinstance(current, Handoff):
                    break
                continue

            _logger.info("route step=%d -> worker=%s", step, worker_name)
            record_step("route", f"step {step}: -> {worker_name}", worker=worker_name)
            if self._checkpointer is not None:
                self._checkpointer.save_step(
                    self._run_id, step, worker_name, current.input, None, STATUS_RUNNING
                )

            try:
                with self._span(f"orchestrate.{worker_name}", step=step):
                    result = await self._workers[worker_name].arun(current.input)
                output = result.output
            except Exception as exc:  # noqa: BLE001 - graceful partial failure
                errors.append(
                    ErrorRecord(
                        error_type=type(exc).__name__,
                        message=str(exc),
                        recovered=False,
                        attempt=step,
                        metadata={"worker": worker_name, "step": step},
                    )
                )
                if self._checkpointer is not None:
                    self._checkpointer.save_step(
                        self._run_id, step, worker_name, current.input, None, STATUS_FAILED
                    )
                if self._dlq is not None:
                    self._dlq.put(input=current.input, error=exc, step=worker_name)
                _logger.warning("worker %s failed at step %d: %s", worker_name, step, exc)
                # Return a partial RunResult rather than crashing.
                return self._assemble(output=None, errors=errors, failed=True)

            if self._checkpointer is not None:
                self._checkpointer.save_step(
                    self._run_id, step, worker_name, current.input, output, STATUS_COMPLETED
                )
            step += 1
            current = output
            if not isinstance(current, Handoff):
                break
        else:
            # while-else: loop exhausted without breaking => max_steps exceeded.
            raise OrchestrationError(
                f"Handoff chain exceeded max_steps={self._max_steps} "
                f"(likely a routing loop) for run {self._run_id}."
            )

        return self._assemble(output=output, errors=errors, failed=False)

    # ------------------------------------------------------------------ #
    def _route(self, input: Any) -> str:
        choice = self._router.route(input, self._workers)
        if choice not in self._workers:
            raise OrchestrationError(
                f"Router returned unknown worker {choice!r}; "
                f"known workers: {sorted(self._workers)}."
            )
        return choice

    def _check_context_size(self, context: Mapping[str, Any]) -> None:
        try:
            size = len(json.dumps(dict(context), default=str).encode("utf-8"))
        except (TypeError, ValueError):
            return  # non-serializable context: skip the size check, don't crash
        if size > _MAX_CONTEXT_BYTES:
            raise OrchestrationError(
                f"Handoff context ({size} bytes) exceeds the "
                f"{_MAX_CONTEXT_BYTES}-byte cap; possible runaway accumulation."
            )

    def _span(self, name: str, **attrs: Any) -> Any:
        if self._tracer is not None:
            return self._tracer.span(name, **attrs)
        from contextlib import nullcontext

        return nullcontext()

    def _assemble(self, *, output: Any, errors: list[ErrorRecord], failed: bool) -> RunResult:
        return RunResult(
            output=output,
            trace_id=self._run_id,
            errors=tuple(errors),
            metadata={"agent_name": self._name, "run_id": self._run_id, "failed": failed},
        )
