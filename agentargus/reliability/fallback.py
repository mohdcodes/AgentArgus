"""Fallback chain: try alternatives on failure (spec §6.4).

The user supplies an ordered list of alternatives (callables or ``BaseAgent``s).
When the primary ``fn`` fails, each alternative is tried in turn; exhausting the
list re-raises the last exception. A fallback is just another callable — the
reliability layer stays framework-agnostic (no model/provider concept).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from agentargus._internal.callables import to_async_callable
from agentargus.logging import get_logger
from agentargus.reliability.base import ReliabilityStrategy, RetryContext

__all__ = ["FallbackChain"]

_logger = get_logger("reliability.fallback")


class FallbackChain(ReliabilityStrategy):
    """Try ``fn`` first, then each alternative in order, on any exception."""

    def __init__(self, alternatives: list[Any]) -> None:
        # Normalise each alternative into an async callable up front.
        self._alternatives = [to_async_callable(a) for a in alternatives]

    async def execute(
        self,
        fn: Callable[[Any], Awaitable[Any]],
        inp: Any,
        ctx: RetryContext,
    ) -> Any:
        candidates = [fn, *self._alternatives]
        last_exc: BaseException | None = None
        for index, candidate in enumerate(candidates):
            try:
                result = await candidate(inp)
                if index > 0:
                    _logger.info("fallback #%d succeeded", index)
                return result
            except BaseException as exc:  # noqa: BLE001 - any failure => try next
                last_exc = exc
                is_last = index == len(candidates) - 1
                ctx.record_error(exc, recovered=not is_last, attempt=index, fallback_index=index)
                with ctx.span(
                    "reliability.fallback",
                    **{"agentargus.fallback_index": index, "agentargus.recovered": not is_last},
                ):
                    pass
                if is_last:
                    _logger.warning("all %d fallbacks exhausted", len(candidates))
                    raise
                _logger.warning(
                    "candidate #%d failed (%s); trying next",
                    index,
                    type(exc).__name__,
                )
        assert last_exc is not None  # unreachable
        raise last_exc
