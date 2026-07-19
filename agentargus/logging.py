"""The AgentArgus logging system (spec §7).

Goals:
    * One factory ``get_logger(name)`` — never call ``logging.getLogger``
      elsewhere (single home / reuse point).
    * Colorized human output for dev, level-based colors, auto-disabled when
      output is not a TTY, when ``NO_COLOR`` is set, or via config.
    * A JSON formatter for production, selectable via config.
    * Every log line inside a run carries the ``trace_id``, propagated through a
      ``contextvars.ContextVar`` (survives async boundaries; keeps trace_id out
      of every function signature). This is the logs<->traces bridge.

Color strategy: raw ANSI codes, no dependency. ``colorama`` was considered for
Windows but modern Windows terminals (Windows Terminal, VS Code, PS 7) support
ANSI natively, and we only emit color when attached to a TTY anyway — so the
extra dependency is not worth it. Recorded in DESIGN_LOG.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from contextvars import ContextVar, Token
from typing import Any

__all__ = [
    "get_logger",
    "configure_logging",
    "set_trace_id",
    "get_trace_id",
    "reset_trace_id",
]

# --------------------------------------------------------------------------- #
# Trace correlation via contextvars
# --------------------------------------------------------------------------- #
_trace_id_var: ContextVar[str | None] = ContextVar("agentargus_trace_id", default=None)


def set_trace_id(trace_id: str | None) -> Token[str | None]:
    """Bind ``trace_id`` for the current context; return a reset token."""
    return _trace_id_var.set(trace_id)


def reset_trace_id(token: Token[str | None]) -> None:
    """Restore the trace_id to what it was before the matching ``set_trace_id``."""
    _trace_id_var.reset(token)


def get_trace_id() -> str | None:
    """Return the trace_id bound to the current context, if any."""
    return _trace_id_var.get()


class _TraceIdFilter(logging.Filter):
    """Injects the current context's trace_id onto every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace_id_var.get()
        return True


# --------------------------------------------------------------------------- #
# Formatters
# --------------------------------------------------------------------------- #
_RESET = "\033[0m"
_LEVEL_COLORS = {
    logging.DEBUG: "\033[90m",  # grey
    logging.INFO: "\033[32m",  # green
    logging.WARNING: "\033[33m",  # yellow
    logging.ERROR: "\033[31m",  # red
    logging.CRITICAL: "\033[1;31m",  # bold red
}


class ColorFormatter(logging.Formatter):
    """Human-readable formatter with optional level-based ANSI color."""

    def __init__(self, use_color: bool) -> None:
        super().__init__()
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        trace_id = getattr(record, "trace_id", None)
        trace_part = f" [{trace_id[:8]}]" if trace_id else ""
        base = (
            f"{self.formatTime(record, '%H:%M:%S')} "
            f"{record.levelname:<8}{trace_part} "
            f"{record.name}: {record.getMessage()}"
        )
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        if self._use_color:
            color = _LEVEL_COLORS.get(record.levelno, "")
            return f"{color}{base}{_RESET}"
        return base


class JsonFormatter(logging.Formatter):
    """Machine-parseable structured formatter for production."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", None),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


# --------------------------------------------------------------------------- #
# Factory / configuration
# --------------------------------------------------------------------------- #
_ROOT_NAME = "agentargus"
_configured = False
_config_lock = threading.Lock()


def _should_use_color(color_flag: bool, stream: Any) -> bool:
    """Color only when requested AND NO_COLOR unset AND the stream is a TTY."""
    if not color_flag:
        return False
    if os.environ.get("NO_COLOR") is not None:
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def configure_logging(
    level: str = "INFO",
    *,
    color: bool = True,
    json_format: bool = False,
    stream: Any | None = None,
) -> None:
    """Configure the ``agentargus`` root logger.

    The contract is "call once at startup". A lock plus build-then-swap makes
    even a concurrent misuse safe: the fully-built handler is installed
    atomically, so logging can never be observed half-configured
    (HARD_QUESTIONS #5).
    """
    global _configured
    stream = stream if stream is not None else sys.stderr

    # Build the new handler completely BEFORE touching the logger.
    handler = logging.StreamHandler(stream)
    handler.addFilter(_TraceIdFilter())
    if json_format:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(ColorFormatter(_should_use_color(color, stream)))

    with _config_lock:
        logger = logging.getLogger(_ROOT_NAME)
        logger.setLevel(level.upper())
        logger.propagate = False
        # Atomic swap: replace the handler list in one assignment rather than
        # remove-then-add as two separately-visible steps.
        logger.handlers = [handler]
        _configured = True


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a namespaced child of the ``agentargus`` logger.

    This is the ONLY sanctioned way to obtain a logger in library code.
    """
    if not _configured:
        configure_logging()
    if name is None or name == _ROOT_NAME:
        return logging.getLogger(_ROOT_NAME)
    return logging.getLogger(f"{_ROOT_NAME}.{name}")
