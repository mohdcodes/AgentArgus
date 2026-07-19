"""Cost tracking (spec §6.3).

``CostTracker`` prices every LLM/tool call whose usage is reported to it, keeps
an **itemized ledger** (which step, how many input/output tokens, what it cost),
sums them into a ``CostBreakdown``, emits a cost sub-span per entry, and enforces
an optional cost ceiling.

Pricing is **user-supplied** (dollars per 1M tokens) via ``register_model`` or a
dict at construction — AgentArgus ships no baked-in prices. Token counts are the
**provider-reported** counts (the standard, billed-on figures).

``add_usage`` is methodoverload site #2: it dispatches on the *shape* of what the
caller has — a raw ``dict``, a typed ``Usage``, or a provider response object.
"""

# NOTE: NO ``from __future__ import annotations`` here — methodoverload
# dispatches on runtime annotations via isinstance, and PEP 563 stringization
# breaks that (see docs/concepts/methodoverload.md). Overloaded parameters keep
# real type annotations.

from dataclasses import dataclass, field
from typing import Any

from methodoverload import overload

from agentargus._internal.exceptions import CostCeilingExceeded
from agentargus._internal.pricing import PriceTable
from agentargus.agents.seams import TracerSeam
from agentargus.core import CostBreakdown
from agentargus.logging import get_logger
from agentargus.observability.conventions import (
    GEN_AI_REQUEST_MODEL,
    GEN_AI_USAGE_COST_USD,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    SPAN_LLM_CALL,
)

__all__ = ["CostTracker", "Usage", "CostEntry"]

_logger = get_logger("observability.cost")


@dataclass(frozen=True, slots=True)
class Usage:
    """A typed token-usage record — the canonical input to ``add_usage``."""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class CostEntry:
    """One itemized row of the cost ledger: which step cost what (spec: per-step).

    This is the "table" the user inspects — step label, model, token counts, and
    the computed cost for that single call.
    """

    step: str
    model: str
    input_tokens: int
    output_tokens: int
    cost: CostBreakdown = field(default_factory=CostBreakdown)


class CostTracker:
    """Prices reported usage, keeps a per-step ledger, and guards a ceiling."""

    def __init__(
        self,
        pricing: dict[str, tuple[float, float]] | None = None,
        *,
        ceiling_usd: float | None = None,
        tracer: TracerSeam | None = None,
    ) -> None:
        self._prices = PriceTable(pricing)
        self._ceiling = ceiling_usd
        self._tracer = tracer
        self._entries: list[CostEntry] = []

    # ------------------------------------------------------------------ #
    # Pricing registration
    # ------------------------------------------------------------------ #
    def register_model(self, model: str, input_per_1m: float, output_per_1m: float) -> None:
        """Register a model's price (dollars per 1M input / output tokens)."""
        self._prices.register(model, input_per_1m, output_per_1m)

    # ------------------------------------------------------------------ #
    # add_usage — methodoverload site #2 (dispatch on the usage shape)
    # ------------------------------------------------------------------ #
    @overload
    def add_usage(self, usage: dict, *, model: str, step: str = "llm_call") -> CostEntry:  # type: ignore[type-arg]  # bare `dict` is REQUIRED: methodoverload dispatches via isinstance, and isinstance(x, dict[str,Any]) raises. See docs/concepts/methodoverload.md (no generics).
        """Accept a raw usage dict: ``{"input_tokens": .., "output_tokens": ..}``."""
        return self._record(
            step=step,
            model=model,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
        )

    @overload  # type: ignore[no-redef]  # methodoverload merges runtime overloads; mypy sees a redefinition
    def add_usage(self, usage: Usage, *, model: str, step: str = "llm_call") -> CostEntry:  # noqa: F811
        """Accept a typed ``Usage`` object."""
        return self._record(
            step=step,
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )

    @overload  # type: ignore[no-redef]
    def add_usage(self, usage: object, *, model: str, step: str = "llm_call") -> CostEntry:  # noqa: F811
        """Catch-all for a provider response object exposing ``.usage``.

        Dispatches on ``object`` (registered last) because provider response
        types vary and share no common base we can import. Reads
        ``usage.input_tokens`` / ``usage.output_tokens`` from a nested ``usage``
        attribute if present, else from the object directly.
        """
        source = getattr(usage, "usage", usage)
        input_tokens = getattr(source, "input_tokens", None)
        output_tokens = getattr(source, "output_tokens", None)
        if input_tokens is None or output_tokens is None:
            raise TypeError(
                "add_usage could not read input_tokens/output_tokens from "
                f"{type(usage).__name__!r}. Pass a dict, a Usage, or a response "
                "object exposing .usage.input_tokens / .usage.output_tokens."
            )
        return self._record(
            step=step,
            model=model,
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
        )

    # ------------------------------------------------------------------ #
    # Internal recording (one home for pricing + ledger + span + ceiling)
    # ------------------------------------------------------------------ #
    def _record(self, *, step: str, model: str, input_tokens: int, output_tokens: int) -> CostEntry:
        cost = self._prices.price(model, input_tokens, output_tokens)
        entry = CostEntry(
            step=step,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
        )
        self._entries.append(entry)

        # Emit a cost sub-span so this priced call is visible in the trace too.
        if self._tracer is not None:
            with self._tracer.span(
                SPAN_LLM_CALL,
                **{
                    GEN_AI_REQUEST_MODEL: model,
                    GEN_AI_USAGE_INPUT_TOKENS: input_tokens,
                    GEN_AI_USAGE_OUTPUT_TOKENS: output_tokens,
                    GEN_AI_USAGE_COST_USD: cost.total_cost,
                    "agentargus.step": step,
                },
            ):
                pass

        _logger.info(
            "cost step=%s model=%s in=%d out=%d cost=$%.6f",
            step,
            model,
            input_tokens,
            output_tokens,
            cost.total_cost,
        )
        self._check_ceiling()
        return entry

    def _check_ceiling(self) -> None:
        if self._ceiling is None:
            return
        running = self.total().total_cost
        if running > self._ceiling:
            _logger.warning("cost ceiling exceeded: $%.4f > $%.4f", running, self._ceiling)
            raise CostCeilingExceeded(running, self._ceiling)

    # ------------------------------------------------------------------ #
    # Read API
    # ------------------------------------------------------------------ #
    @property
    def entries(self) -> tuple[CostEntry, ...]:
        """The itemized per-step ledger (immutable view)."""
        return tuple(self._entries)

    def total(self) -> CostBreakdown:
        """The aggregate cost across every recorded entry."""
        summed = CostBreakdown()
        for entry in self._entries:
            summed = summed + entry.cost
        return summed

    def table(self) -> list[dict[str, Any]]:
        """The ledger as plain rows (for display / RunResult.metadata)."""
        return [
            {
                "step": e.step,
                "model": e.model,
                "input_tokens": e.input_tokens,
                "output_tokens": e.output_tokens,
                "cost_usd": round(e.cost.total_cost, 6),
            }
            for e in self._entries
        ]
