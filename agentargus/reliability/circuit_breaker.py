"""Circuit breaker (spec §6.4) — the encapsulation pillar.

The ``CLOSED → OPEN → HALF_OPEN`` state machine is hidden behind
``allow()`` / ``record_success()`` / ``record_failure()``. Internal counters are
name-mangled and every transition is guarded by a lock, so concurrent calls
(e.g. 20 parallel synthesis calls hitting a failing endpoint) cannot corrupt the
state. When OPEN the breaker fails fast with ``CircuitOpenError`` — it does not
call the wrapped function.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

from agentargus._internal.exceptions import CircuitOpenError
from agentargus.logging import get_logger
from agentargus.reliability.base import ReliabilityStrategy, RetryContext

__all__ = ["CircuitBreaker"]

_logger = get_logger("reliability.circuit_breaker")

_CLOSED = "CLOSED"
_OPEN = "OPEN"
_HALF_OPEN = "HALF_OPEN"


class CircuitBreaker(ReliabilityStrategy):
    """Fail fast when a dependency looks down; probe for recovery after cooldown.

    A monotonic clock is injectable for deterministic tests.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        *,
        cooldown: float = 30.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self.__threshold = failure_threshold
        self.__cooldown = cooldown
        self.__clock = clock or time.monotonic
        self.__state = _CLOSED
        self.__consecutive_failures = 0
        self.__opened_at = 0.0
        self.__lock = threading.Lock()

    @property
    def state(self) -> str:
        """Current state (read-only view; primarily for tests/introspection)."""
        with self.__lock:
            return self.__state

    def allow(self) -> bool:
        """Return whether a call may proceed; transitions OPEN→HALF_OPEN on cooldown."""
        with self.__lock:
            if self.__state == _OPEN:
                if self.__clock() - self.__opened_at >= self.__cooldown:
                    self.__state = _HALF_OPEN
                    _logger.info("circuit HALF_OPEN (cooldown elapsed)")
                    return True  # allow a single trial
                return False
            # CLOSED or HALF_OPEN both allow a call through.
            return True

    def record_success(self) -> None:
        with self.__lock:
            self.__consecutive_failures = 0
            if self.__state != _CLOSED:
                _logger.info("circuit CLOSED (trial succeeded)")
            self.__state = _CLOSED

    def record_failure(self) -> None:
        with self.__lock:
            self.__consecutive_failures += 1
            if self.__state == _HALF_OPEN:
                # Trial failed → straight back to OPEN.
                self.__state = _OPEN
                self.__opened_at = self.__clock()
                _logger.warning("circuit OPEN (trial failed)")
            elif self.__consecutive_failures >= self.__threshold:
                self.__state = _OPEN
                self.__opened_at = self.__clock()
                _logger.warning(
                    "circuit OPEN (%d consecutive failures)", self.__consecutive_failures
                )

    async def execute(
        self,
        fn: Callable[[Any], Awaitable[Any]],
        inp: Any,
        ctx: RetryContext,
    ) -> Any:
        if not self.allow():
            exc = CircuitOpenError("Circuit is OPEN; refusing call to protect the dependency.")
            ctx.record_error(exc, recovered=False)
            with ctx.span("reliability.circuit_open"):
                pass
            raise exc
        try:
            result = await fn(inp)
        except BaseException:  # noqa: BLE001 - any failure counts toward tripping
            self.record_failure()
            raise
        self.record_success()
        return result
