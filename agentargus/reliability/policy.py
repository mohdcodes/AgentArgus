"""ReliabilityPolicy — composes strategies and applies them (spec §6.4).

Composition order (from the start-of-module design answers):
``breaker(fallback(retry(fn)))`` — retry a candidate a few times, then fall back
to the next candidate, with the breaker gating the whole thing; the DLQ captures
whatever still fails. This is the polymorphism pillar: the policy holds a
``list[ReliabilityStrategy]`` and drives them without knowing concrete types.

The policy is the real ``ReliabilitySeam``: it is an async callable
``(fn, inp) -> output`` that ``Agent.arun`` invokes unchanged. It also exposes
the ``ErrorRecord``s it accumulated (via the last ``RetryContext``) so the Agent
can attach them to ``RunResult.errors``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from agentargus.core import ErrorRecord
from agentargus.logging import get_logger
from agentargus.reliability.base import ReliabilityStrategy, RetryContext
from agentargus.reliability.circuit_breaker import CircuitBreaker
from agentargus.reliability.dead_letter import DeadLetterQueue, DeadLetterSink
from agentargus.reliability.fallback import FallbackChain
from agentargus.reliability.retry import RetryWithBackoff

if TYPE_CHECKING:
    from agentargus.agents.seams import TracerSeam

__all__ = ["ReliabilityPolicy"]

_logger = get_logger("reliability.policy")


class ReliabilityPolicy:
    """Composes retry / fallback / breaker / DLQ into one resilient callable."""

    def __init__(
        self,
        *,
        retries: int | None = None,
        retry: RetryWithBackoff | None = None,
        fallbacks: list[Any] | None = None,
        breaker: CircuitBreaker | None = None,
        dead_letter: DeadLetterSink | None = None,
        tracer: TracerSeam | None = None,
        step: str = "call",
    ) -> None:
        # Retry: accept either a ready strategy or a simple attempt count.
        if retry is not None:
            self._retry: RetryWithBackoff | None = retry
        elif retries is not None:
            self._retry = RetryWithBackoff(max_attempts=retries)
        else:
            self._retry = None

        self._fallback = FallbackChain(fallbacks) if fallbacks else None
        self._breaker = breaker
        self._dlq = DeadLetterQueue(dead_letter) if dead_letter is not None else None
        self._tracer = tracer
        self._step = step
        self._last_errors: list[ErrorRecord] = []

    @property
    def last_errors(self) -> tuple[ErrorRecord, ...]:
        """ErrorRecords accumulated during the most recent call (for RunResult)."""
        return tuple(self._last_errors)

    def _ordered_strategies(self) -> list[ReliabilityStrategy]:
        """Strategies from OUTERMOST to INNERMOST: breaker, fallback, retry."""
        ordered: list[ReliabilityStrategy] = []
        if self._breaker is not None:
            ordered.append(self._breaker)
        if self._fallback is not None:
            ordered.append(self._fallback)
        if self._retry is not None:
            ordered.append(self._retry)
        return ordered

    async def __call__(self, fn: Callable[[Any], Awaitable[Any]], inp: Any) -> Any:
        ctx = RetryContext(step=self._step, tracer=self._tracer)
        # Compose inside-out: retry is innermost, breaker outermost. Each
        # strategy wraps the accumulated callable so calling the outermost runs
        # the full stack.
        wrapped = fn
        for strategy in reversed(self._ordered_strategies()):
            wrapped = self._wrap(strategy, wrapped, ctx)
        try:
            return await wrapped(inp)
        except BaseException as exc:  # noqa: BLE001 - terminal failure
            if self._dlq is not None:
                self._dlq.put(input=inp, error=exc, step=self._step)
            raise
        finally:
            self._last_errors = ctx.errors

    @staticmethod
    def _wrap(
        strategy: ReliabilityStrategy,
        inner: Callable[[Any], Awaitable[Any]],
        ctx: RetryContext,
    ) -> Callable[[Any], Awaitable[Any]]:
        async def call(inp: Any) -> Any:
            return await strategy.execute(inner, inp, ctx)

        return call
