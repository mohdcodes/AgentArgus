"""Central configuration object for AgentArgus.

``AgentArgusConfig`` is populated from environment variables with explicit
keyword overrides taking precedence. It encapsulates cross-cutting settings so
no module reaches into ``os.environ`` directly — one home for configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agentargus._internal.exceptions import ConfigError

__all__ = ["AgentArgusConfig", "Judge", "Embedder", "batch_complete"]


@runtime_checkable
class Judge(Protocol):
    """The seam for LLM-as-judge evaluation.

    AgentArgus ships **no** provider client in the base package (spec §1/§9 —
    framework-agnostic, minimal deps). Metrics that need an LLM depend on this
    protocol; the user injects any implementation. A thin Anthropic adapter is
    provided behind the ``[dev]`` extra for tests and the demo.
    """

    def complete(self, prompt: str) -> str:
        """Return the model's text completion for ``prompt``.

        This is the ONLY required member. An adapter may *optionally* also define
        ``complete_batch(prompts: list[str]) -> list[str]`` to parallelize large
        eval datasets — but it is intentionally NOT part of this protocol, so
        that a minimal ``complete``-only adapter still satisfies ``isinstance``
        checks (HARD_QUESTIONS #6). Call sites use the ``batch_complete`` helper,
        which probes for ``complete_batch`` and falls back to looping.
        """
        ...


@runtime_checkable
class Embedder(Protocol):
    """Optional seam for text embeddings (used by RAGAS-style AnswerRelevance).

    Like ``Judge``, no embedding backend ships in the base package. Inject any
    implementation whose ``embed`` maps texts to vectors; AnswerRelevance then
    uses cosine similarity exactly as RAGAS does. Absent → AnswerRelevance falls
    back to a judge-scored relevance.
    """

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""
        ...


def batch_complete(judge: Judge, prompts: list[str]) -> list[str]:
    """Complete many prompts via a judge, using ``complete_batch`` if provided.

    This is the single call-site seam for batched judging (one home). If the
    adapter implements ``complete_batch`` it is used (potentially concurrent);
    otherwise we loop ``complete`` so simple adapters still work. Later modules
    (eval) call this rather than deciding batching per metric.
    """
    batch = getattr(judge, "complete_batch", None)
    if callable(batch):
        result = batch(prompts)
        # A Protocol member that is present but unimplemented may return the
        # Ellipsis sentinel; fall back to the loop in that degenerate case.
        if isinstance(result, list):
            return result
    return [judge.complete(p) for p in prompts]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float_strict(name: str, default: float | None) -> float | None:
    """Parse a float env var, or raise ConfigError on a malformed value.

    Fail-fast is deliberate for safety-critical settings like a cost ceiling: a
    silent fallback could let an app run with the wrong spend limit and only
    surface the mistake after money has been spent (HARD_QUESTIONS #9).
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(
            f"{name} must be a number, got {raw!r}. "
            f"Refusing to start with an ambiguous safety limit."
        ) from exc


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
            cost_ceiling_usd=_env_float_strict("AGENTARGUS_COST_CEILING_USD", None),
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
