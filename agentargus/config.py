"""Central configuration object for AgentArgus.

``AgentArgusConfig`` is populated from environment variables with explicit
keyword overrides taking precedence. It encapsulates cross-cutting settings so
no module reaches into ``os.environ`` directly — one home for configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

__all__ = ["AgentArgusConfig", "Judge"]


@runtime_checkable
class Judge(Protocol):
    """The seam for LLM-as-judge evaluation.

    AgentArgus ships **no** provider client in the base package (spec §1/§9 —
    framework-agnostic, minimal deps). Metrics that need an LLM depend on this
    protocol; the user injects any implementation. A thin Anthropic adapter is
    provided behind the ``[dev]`` extra for tests and the demo.
    """

    def complete(self, prompt: str) -> str:
        """Return the model's text completion for ``prompt``."""
        ...


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float | None) -> float | None:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class AgentArgusConfig:
    """Cross-cutting configuration, resolved from env + explicit kwargs.

    Precedence: an explicitly-passed value wins; otherwise the environment is
    consulted via ``from_env``; otherwise a sane default is used.
    """

    judge_model: str = "claude-opus-4-8"
    judge: Judge | None = None
    cost_ceiling_usd: float | None = None
    tracer_exporter: str = "console"  # "console" | "otlp" | "memory"
    log_level: str = "INFO"
    log_color: bool = True
    log_json: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls, **overrides: Any) -> AgentArgusConfig:
        """Build a config from the environment, then apply explicit overrides."""
        base = cls(
            judge_model=os.environ.get("AGENTARGUS_JUDGE_MODEL", "claude-opus-4-8"),
            cost_ceiling_usd=_env_float("AGENTARGUS_COST_CEILING_USD", None),
            tracer_exporter=os.environ.get("AGENTARGUS_TRACER_EXPORTER", "console"),
            log_level=os.environ.get("AGENTARGUS_LOG_LEVEL", "INFO"),
            log_color=_env_bool("AGENTARGUS_LOG_COLOR", True),
            log_json=_env_bool("AGENTARGUS_LOG_JSON", False),
        )
        for key, value in overrides.items():
            if not hasattr(base, key):
                raise TypeError(f"Unknown config option: {key!r}")
            setattr(base, key, value)
        return base
