"""EvalSuite (spec §6.5) — the polymorphism pillar.

Holds a ``list[Metric]`` and computes them all, calling ``metric.compute`` without
knowing the concrete type. Metrics that report ``NOT_APPLICABLE`` (NaN) for a
given input are excluded from the scores dict rather than recorded as a
misleading number.
"""

from __future__ import annotations

import math
from typing import Any

from agentargus.core import RunResult
from agentargus.eval.metrics.base import Metric
from agentargus.logging import get_logger

__all__ = ["EvalSuite"]

_logger = get_logger("eval.suite")


class EvalSuite:
    """A collection of metrics applied uniformly to a run (or test dict)."""

    def __init__(self, metrics: list[Metric]) -> None:
        self.metrics = metrics

    def run(self, source: RunResult | dict[str, Any]) -> dict[str, float]:
        """Compute every metric; return ``{name: score}`` (NaN scores dropped)."""
        scores: dict[str, float] = {}
        for metric in self.metrics:
            value = metric.compute(source)
            if isinstance(value, float) and math.isnan(value):
                _logger.info("metric %s not applicable; excluded", metric.name)
                continue
            scores[metric.name] = value
        return scores

    def score(self, result: RunResult) -> RunResult:
        """Convenience: run the suite and return a NEW RunResult with the scores."""
        return result.with_scores(self.run(result))
