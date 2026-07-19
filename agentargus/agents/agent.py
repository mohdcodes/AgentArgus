"""The ``Agent`` facade (spec §6.1) — the central abstraction of AgentArgus.

``Agent`` wraps *any* target (a ``BaseAgent``, or an arbitrary sync/async
callable) and decorates its execution with observability, reliability, and
(later) HITL, producing a rich ``RunResult`` that eval consumes.

Design notes:
*   **Composition over inheritance** for collaborators — ``Agent`` *has* a
    tracer / cost tracker / reliability policy; it is not one. They are injected
    as null objects now (see ``seams.py``) and swapped for real implementations
    in later modules with no change to this file.
*   **Async-core, sync-wraps** — all orchestration lives in ``arun``; ``run`` is
    inherited from ``BaseAgent`` and drives ``arun``.
*   **``wrap`` is methodoverload site #3.** We overload on ``BaseAgent`` (clean
    ``isinstance`` dispatch, verified). Plain callables have no distinct
    ``isinstance`` class, so they are handled by a non-overloaded fallback —
    exactly the honest limitation spec §4.3 anticipates. See DESIGN_LOG.
"""

# NOTE: this module deliberately does NOT use ``from __future__ import
# annotations``. That import stringizes all annotations (PEP 563), and
# methodoverload dispatches via ``isinstance(value, param.annotation)`` at
# runtime — a stringized annotation is not a type, so dispatch raises
# "isinstance() arg 2 must be a type". Every module with an @overload site must
# keep real (non-stringized) annotations on the overloaded parameters. See
# DESIGN_LOG (Module 1) and docs/concepts/methodoverload.md.

import asyncio
import inspect
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from methodoverload import overload

from agentargus.agents.base import BaseAgent
from agentargus.agents.seams import (
    CostSeam,
    NullCostTracker,
    NullTracer,
    PassthroughReliability,
    ReliabilitySeam,
    TracerSeam,
)
from agentargus.core import RunResult
from agentargus.logging import get_logger, reset_trace_id, set_trace_id
from agentargus.observability.conventions import (
    GEN_AI_OPERATION_NAME,
    OP_INVOKE_AGENT,
    SPAN_AGENT_RUN,
)

__all__ = ["Agent"]

_logger = get_logger("agents")


def _new_trace_id() -> str:
    """Generate a fresh correlation id for a run (uuid4 hex)."""
    return uuid.uuid4().hex


class Agent(BaseAgent):
    """Wraps an inner target and runs it with observability + reliability.

    The inner target is normalised at construction time into a single async
    callable ``self._call_inner(input) -> output`` regardless of whether the
    user passed a ``BaseAgent``, a sync function, or an async function.
    """

    def __init__(
        self,
        inner: Any,
        *,
        tracer: TracerSeam | None = None,
        cost: CostSeam | None = None,
        reliability: ReliabilitySeam | None = None,
        name: str | None = None,
    ) -> None:
        self._inner = inner
        self._name = name or getattr(inner, "__name__", type(inner).__name__)
        # Null objects by default; real collaborators arrive in later modules.
        self._tracer: TracerSeam = tracer or NullTracer()
        self._cost: CostSeam = cost or NullCostTracker()
        self._reliability: ReliabilitySeam = reliability or PassthroughReliability()
        self._call_inner = self.wrap(inner)

    # ------------------------------------------------------------------ #
    # wrap() — methodoverload site #3
    # ------------------------------------------------------------------ #
    @overload
    def wrap(self, inner: BaseAgent) -> Callable[[Any], Awaitable[Any]]:
        """Wrap another ``BaseAgent``: delegate to its own ``arun`` output.

        The inner agent already returns a ``RunResult``; we surface its
        ``output`` so this facade's own ``RunResult`` wraps it uniformly.
        """

        async def call(inp: Any) -> Any:
            inner_result = await inner.arun(inp)
            return inner_result.output

        return call

    @overload  # type: ignore[no-redef]  # methodoverload merges same-named @overloads at runtime; mypy models it as a redefinition
    def wrap(self, inner: object) -> Callable[[Any], Awaitable[Any]]:  # noqa: F811
        """Catch-all for arbitrary callables (functions, lambdas, ``__call__``).

        This IS an ``@overload`` sibling — but it dispatches on ``object``, not a
        fictional ``Callable`` type, because callables share no distinct
        ``isinstance`` class (verified). ``object`` matches anything; since the
        ``BaseAgent`` overload is registered first and first-match-wins, only
        non-``BaseAgent`` targets reach here. (A plain, undecorated method would
        instead OVERWRITE the ``BaseAgent`` overload — the library only merges
        ``@overload``-decorated siblings — so both must be decorated. See
        DESIGN_LOG.) Both sync and async callables are supported; sync ones run
        via ``to_thread`` so they never block the event loop.
        """
        if not callable(inner):
            raise TypeError(
                f"Agent inner target must be a BaseAgent or a callable, "
                f"got {type(inner).__name__!r}."
            )

        if inspect.iscoroutinefunction(inner):

            async def call_async(inp: Any) -> Any:
                return await inner(inp)

            return call_async

        async def call_sync(inp: Any) -> Any:
            # Run blocking sync work off the event loop.
            return await asyncio.to_thread(inner, inp)

        return call_sync

    # ------------------------------------------------------------------ #
    # Orchestration — written in final shape; seams are null objects for now
    # ------------------------------------------------------------------ #
    async def arun(self, input: Any) -> RunResult:
        # Trace model: exactly ONE trace_id per arun() call — including a nested
        # inner agent's own arun(). When agents nest, trace_ids NEST rather than
        # compete: the inner call's set_trace_id shadows the outer's for its
        # duration, and reset_trace_id (via the contextvar token) restores the
        # outer's afterward. So logs emitted during the inner run carry the inner
        # id; logs during the outer run carry the outer id. Module 2's OTel
        # Tracer formalises this as a parent/child span tree; today they are
        # independent-but-nested ids, never merged.
        # Placeholder id used only for log correlation until we learn the real
        # OTel trace id inside the span (the tracer's id is the source of truth
        # when tracing is active; NullTracer returns None so this uuid4 stands).
        fallback_id = _new_trace_id()
        token = set_trace_id(fallback_id)
        spans: tuple[Any, ...] = ()
        try:
            _logger.debug("agent.run start name=%s", self._name)
            with self._tracer.span(SPAN_AGENT_RUN, **{GEN_AI_OPERATION_NAME: OP_INVOKE_AGENT}):
                # Adopt the real trace id as soon as the span is open so that
                # logs emitted during the run correlate to the OTel trace.
                otel_id = self._tracer.current_trace_id()
                if otel_id is not None:
                    set_trace_id(otel_id)
                output = await self._reliability(self._call_inner, input)
                cost = self._cost.total()
            trace_id = otel_id or fallback_id
            spans = self._tracer.collect(trace_id)
            _logger.info("agent.run done name=%s cost=$%.4f", self._name, cost.total_cost)
            return RunResult(
                output=output,
                trace_id=trace_id,
                spans=spans,
                cost=cost,
                metadata={"agent_name": self._name},
            )
        finally:
            reset_trace_id(token)
