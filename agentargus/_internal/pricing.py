"""PRIVATE per-model price table (spec §6.3, encapsulation pillar).

Prices are **user-supplied**, expressed as dollars **per 1,000,000 tokens** —
one rate for input tokens, one for output. AgentArgus ships **no** baked-in
prices: provider pricing drifts, and guessing a stale number is worse than
asking the user for the truth. This keeps cost accounting honest and removes any
"unknown/outdated model price" maintenance burden.

This module is private. Nothing outside ``observability/`` imports it — callers
interact only through ``CostTracker`` (spec §3: pricing tables are encapsulated).
"""

from __future__ import annotations

from agentargus.core import CostBreakdown
from agentargus.logging import get_logger

__all__ = ["PriceTable"]

_logger = get_logger("observability.pricing")

_PER_MILLION = 1_000_000


class PriceTable:
    """Maps a model name to its (input_per_1m, output_per_1m) dollar rates."""

    def __init__(self, pricing: dict[str, tuple[float, float]] | None = None) -> None:
        # Copy so external mutation of the caller's dict can't change our table.
        self._prices: dict[str, tuple[float, float]] = dict(pricing or {})

    def register(self, model: str, input_per_1m: float, output_per_1m: float) -> None:
        """Add or replace the price for ``model`` (dollars per 1M tokens)."""
        if input_per_1m < 0 or output_per_1m < 0:
            raise ValueError("Prices must be non-negative.")
        self._prices[model] = (input_per_1m, output_per_1m)

    def has(self, model: str) -> bool:
        return model in self._prices

    def price(self, model: str, input_tokens: int, output_tokens: int) -> CostBreakdown:
        """Compute the cost for a usage on ``model``.

        Unknown model: the tokens are still counted (they are useful) but cost is
        $0.00 and a WARNING is logged naming the model — never silently pretend
        the cost is known, never crash the run over a missing price.
        """
        rates = self._prices.get(model)
        if rates is None:
            _logger.warning(
                "No price registered for model %r; counting tokens with $0.00 "
                "cost. Register it via CostTracker.register_model(...).",
                model,
            )
            return CostBreakdown(input_tokens=input_tokens, output_tokens=output_tokens)
        in_per_1m, out_per_1m = rates
        return CostBreakdown(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost=input_tokens / _PER_MILLION * in_per_1m,
            output_cost=output_tokens / _PER_MILLION * out_per_1m,
        )
