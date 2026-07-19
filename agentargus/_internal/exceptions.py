"""Internal exception hierarchy for AgentArgus.

One home for the library's own error types. ``NoMatchingOverloadError`` is NOT
redefined here — it is imported directly from ``methodoverload`` at its use
sites (spec §9 is explicit about this).
"""

from __future__ import annotations

__all__ = ["AgentArgusError", "ConfigError", "SerializationError"]


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
