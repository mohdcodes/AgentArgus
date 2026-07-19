"""Reliability abstraction (spec §6.4).

``ReliabilityStrategy`` is the common interface every resilience component
implements (retry, fallback, circuit breaker). ``ReliabilityPolicy`` composes a
list of them and applies them uniformly — the polymorphism pillar.

``RetryContext`` carries the per-run state a strategy needs while it runs: the
shared tracer (so retries/trips/fallbacks emit spans nested under ``agent.run``),
an error accumulator (every failed attempt becomes an ``ErrorRecord`` that ends
up on ``RunResult.errors``), and a step label for attribution.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agentargus.core import ErrorRecord

if TYPE_CHECKING:
    from collections.abc import Iterator

    from agentargus.agents.seams import TracerSeam

__all__ = ["ReliabilityStrategy", "RetryContext"]


@dataclass
class RetryContext:
    """Mutable per-run context threaded through the composed strategies."""

    step: str = "call"
    tracer: TracerSeam | None = None
    errors: list[ErrorRecord] = field(default_factory=list)

    def record_error(
        self,
        exc: BaseException,
        *,
        recovered: bool,
        attempt: int = 0,
        **metadata: Any,
    ) -> None:
        """Append an ``ErrorRecord`` describing one failed attempt."""
        self.errors.append(
            ErrorRecord(
                error_type=type(exc).__name__,
                message=str(exc),
                recovered=recovered,
                attempt=attempt,
                metadata={"step": self.step, **metadata},
            )
        )

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[None]:
        """Emit a reliability span if a tracer is present; else a no-op."""
        if self.tracer is None:
            yield None
            return
        with self.tracer.span(name, **attributes):
            yield None


class ReliabilityStrategy(ABC):
    """A resilience component that wraps the execution of ``fn``."""

    @abstractmethod
    async def execute(
        self,
        fn: Callable[[Any], Awaitable[Any]],
        inp: Any,
        ctx: RetryContext,
    ) -> Any:
        """Run ``fn(inp)`` under this strategy; return its result or re-raise."""
        raise NotImplementedError
