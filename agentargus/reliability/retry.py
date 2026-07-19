"""Retry with exponential backoff + jitter (spec §6.4)."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any

from agentargus._internal.exceptions import TransientError
from agentargus.logging import get_logger
from agentargus.reliability.base import ReliabilityStrategy, RetryContext

__all__ = ["RetryWithBackoff"]

_logger = get_logger("reliability.retry")

# Default retryable set: transient/network failures — NOT programming errors
# (ValueError/TypeError etc.), which never succeed on retry and would waste
# attempts + money. User-overridable.
DEFAULT_RETRYABLE: tuple[type[BaseException], ...] = (
    TransientError,
    TimeoutError,
    ConnectionError,
)


class RetryWithBackoff(ReliabilityStrategy):
    """Retry ``fn`` up to ``max_attempts`` with exponential backoff and jitter."""

    def __init__(
        self,
        max_attempts: int = 3,
        *,
        base_delay: float = 0.5,
        max_delay: float = 30.0,
        jitter: float = 0.1,
        retryable: tuple[type[BaseException], ...] = DEFAULT_RETRYABLE,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._max_attempts = max_attempts
        self._base = base_delay
        self._max_delay = max_delay
        self._jitter = jitter
        self._retryable = retryable
        # Injectable sleeper so tests don't wait real seconds.
        self._sleep = sleep or asyncio.sleep

    def _delay_for(self, attempt: int) -> float:
        raw: float = self._base * float(2 ** (attempt - 1))
        raw = min(raw, self._max_delay)
        # random() only varies the jitter fraction; determinism-sensitive tests
        # inject sleep=... and ignore the delay value anyway.
        jitter: float = raw * self._jitter * (2.0 * random.random() - 1.0)  # noqa: S311
        return max(0.0, raw + jitter)

    async def execute(
        self,
        fn: Callable[[Any], Awaitable[Any]],
        inp: Any,
        ctx: RetryContext,
    ) -> Any:
        last_exc: BaseException | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                result = await fn(inp)
                if attempt > 1:
                    _logger.info("retry succeeded on attempt %d", attempt)
                return result
            except self._retryable as exc:
                last_exc = exc
                is_last = attempt == self._max_attempts
                ctx.record_error(exc, recovered=not is_last, attempt=attempt)
                with ctx.span(
                    "reliability.retry",
                    **{"agentargus.attempt": attempt, "agentargus.recovered": not is_last},
                ):
                    pass
                if is_last:
                    _logger.warning("retry exhausted after %d attempts", attempt)
                    raise
                delay = self._delay_for(attempt)
                _logger.warning(
                    "attempt %d failed (%s); retrying in %.2fs",
                    attempt,
                    type(exc).__name__,
                    delay,
                )
                await self._sleep(delay)
            except BaseException as exc:  # noqa: BLE001 - non-retryable: fail fast
                # Not in the retryable set → do not waste attempts.
                ctx.record_error(exc, recovered=False, attempt=attempt)
                raise
        # Unreachable (loop either returns or raises), but satisfies typing.
        assert last_exc is not None
        raise last_exc
