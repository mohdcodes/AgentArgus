"""Internal exception hierarchy for AgentArgus.

One home for the library's own error types. ``NoMatchingOverloadError`` is NOT
redefined here — it is imported directly from ``methodoverload`` at its use
sites (spec §9 is explicit about this).
"""

from __future__ import annotations

__all__ = [
    "AgentArgusError",
    "ConfigError",
    "SerializationError",
    "CostCeilingExceeded",
]


class AgentArgusError(Exception):
    """Base class for all AgentArgus-raised errors."""


class ConfigError(AgentArgusError):
    """Raised when configuration is invalid — fail fast rather than boot wrong.

    Used for safety-critical settings (e.g. a malformed cost ceiling) where a
    silent fallback to a default could let an app run with the wrong limit and
    only surface the mistake after money has been spent.
    """


class SerializationError(AgentArgusError):
    """Raised when a value cannot be serialized, naming the offending field.

    Preferred over a silent lossy fallback (e.g. ``str(obj)``), which would hide
    real data loss from the caller.
    """


class CostCeilingExceeded(AgentArgusError):
    """Raised when accumulated spend crosses the configured cost ceiling.

    Thrown the moment an ``add_usage`` call would push the running total over the
    limit, so a runaway agent stops spending rather than discovering the overrun
    after the fact. The reliability layer (Module 4) may catch this as a
    controlled failure recorded in ``RunResult.errors``.
    """

    def __init__(self, total_usd: float, ceiling_usd: float) -> None:
        self.total_usd = total_usd
        self.ceiling_usd = ceiling_usd
        super().__init__(
            f"Cost ceiling exceeded: ${total_usd:.4f} would exceed the "
            f"${ceiling_usd:.4f} limit. Halting to prevent further spend."
        )
