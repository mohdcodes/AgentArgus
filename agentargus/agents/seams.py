"""Null-object seams for the Agent facade.

Module 1 builds the ``Agent`` orchestration in its **final shape**, but the
collaborators it calls (tracer, cost tracker, reliability policy, HITL) arrive
in later modules. Rather than litter ``Agent.arun`` with ``if self._x is not
None`` checks that would never go away, we inject **null objects**: minimal
implementations that satisfy the seam contract and do nothing.

When Module 2 (tracer), 3 (cost), 4 (reliability), and 9 (HITL) land, they
provide real objects with the same interface and are swapped in with **zero
changes** to ``Agent``. The null objects also double as the documented contract
each real collaborator must honour.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Protocol

from agentargus.core import CostBreakdown

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "TracerSeam",
    "CostSeam",
    "ReliabilitySeam",
    "NullTracer",
    "NullCostTracker",
    "PassthroughReliability",
]


class TracerSeam(Protocol):
    """Contract for the observability tracer (real impl: Module 2)."""

    def span(self, name: str, **attributes: Any) -> Any:
        """Return a context manager representing one execution span."""
        ...


class CostSeam(Protocol):
    """Contract for the cost tracker (real impl: Module 3)."""

    def total(self) -> CostBreakdown:
        """Return the accumulated cost for the current run."""
        ...


class ReliabilitySeam(Protocol):
    """Contract for the reliability policy (real impl: Module 4).

    A reliability policy is an async callable that runs ``fn(inp)`` under
    whatever strategies it composes (retry, fallback, breaker) and returns the
    result — or re-raises if it cannot recover.
    """

    async def __call__(self, fn: Callable[[Any], Awaitable[Any]], inp: Any) -> Any: ...


class NullTracer:
    """A tracer that records nothing. Its ``span`` is a no-op context manager."""

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[None]:
        yield None


class NullCostTracker:
    """A cost tracker that always reports zero cost."""

    def total(self) -> CostBreakdown:
        return CostBreakdown()


class PassthroughReliability:
    """A reliability policy that applies no strategies — just calls ``fn``."""

    async def __call__(self, fn: Callable[[Any], Awaitable[Any]], inp: Any) -> Any:
        return await fn(inp)
