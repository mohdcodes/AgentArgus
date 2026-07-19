"""Run recorder (spec §6, Module 7).

The inner agent records its tool calls and reasoning steps so agent-behaviour
metrics (ToolUseAccuracy, ToolSuccessRate, PlanCoherence) have real data to score.
A ``Recorder`` is bound to a ``contextvars.ContextVar`` for the duration of a run
(same pattern as ``set_trace_id``), so the user's inner function can call the
module-level ``record_tool_call`` / ``record_step`` without threading an object
through its signature. ``Agent.arun`` collects the recorder's output onto the
``RunResult``.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from agentargus.core import Step, ToolCall
from agentargus.logging import get_logger

__all__ = [
    "Recorder",
    "current_recorder",
    "set_recorder",
    "reset_recorder",
    "record_tool_call",
    "record_step",
]

_logger = get_logger("agents.recorder")

_recorder_var: ContextVar[Recorder | None] = ContextVar("agentargus_recorder", default=None)


class Recorder:
    """Collects tool calls and steps emitted during one agent run."""

    def __init__(self) -> None:
        self._tool_calls: list[ToolCall] = []
        self._steps: list[Step] = []

    def record_tool_call(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        result: Any = None,
        *,
        success: bool = True,
        latency: float = 0.0,
        error: str | None = None,
    ) -> None:
        self._tool_calls.append(
            ToolCall(
                name=name,
                args=args or {},
                result=result,
                success=success,
                latency=latency,
                error=error,
            )
        )

    def record_step(self, kind: str, content: str, **metadata: Any) -> None:
        self._steps.append(
            Step(index=len(self._steps), kind=kind, content=content, metadata=metadata)
        )

    @property
    def tool_calls(self) -> tuple[ToolCall, ...]:
        return tuple(self._tool_calls)

    @property
    def steps(self) -> tuple[Step, ...]:
        return tuple(self._steps)


# --------------------------------------------------------------------------- #
# Contextvar binding (mirrors logging.set_trace_id / reset_trace_id)
# --------------------------------------------------------------------------- #
def set_recorder(recorder: Recorder | None) -> Token[Recorder | None]:
    """Bind ``recorder`` for the current context; return a reset token."""
    return _recorder_var.set(recorder)


def reset_recorder(token: Token[Recorder | None]) -> None:
    """Restore the recorder to what it was before the matching ``set_recorder``."""
    _recorder_var.reset(token)


def current_recorder() -> Recorder | None:
    """Return the recorder bound to the current context, if any."""
    return _recorder_var.get()


# --------------------------------------------------------------------------- #
# Module-level convenience — what user inner functions call
# --------------------------------------------------------------------------- #
def record_tool_call(
    name: str,
    args: dict[str, Any] | None = None,
    result: Any = None,
    *,
    success: bool = True,
    latency: float = 0.0,
    error: str | None = None,
    recorder: Recorder | None = None,
) -> None:
    """Record a tool call to the active recorder (or an explicit ``recorder``).

    NOTE on sync inner fns: unlike the ``trace_id`` contextvar (which sync inner
    functions running under ``asyncio.to_thread`` do NOT see), the recorder DOES
    work from a ``to_thread`` worker — ``to_thread`` copies the context into the
    worker, and because the ``Recorder`` is a *mutable object* referenced by the
    var (we append to it, never rebind the var), those appends land on the same
    instance ``Agent.arun`` reads back. ``recorder=`` is still offered for cases
    the contextvar genuinely doesn't reach (a raw ``threading.Thread``).
    """
    target = recorder or current_recorder()
    if target is None:
        _logger.debug("record_tool_call(%s) with no active recorder; ignored", name)
        return
    target.record_tool_call(name, args, result, success=success, latency=latency, error=error)


def record_step(
    kind: str, content: str, *, recorder: Recorder | None = None, **metadata: Any
) -> None:
    """Record a reasoning/action step to the active recorder (or explicit one)."""
    target = recorder or current_recorder()
    if target is None:
        _logger.debug("record_step(%s) with no active recorder; ignored", kind)
        return
    target.record_step(kind, content, **metadata)
