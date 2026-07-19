"""Eval report (spec §6.5): summary, regression detection, HTML render."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from agentargus.logging import get_logger

if TYPE_CHECKING:
    from agentargus.eval.runner import CaseResult

__all__ = ["EvalReport"]

_logger = get_logger("eval.report")

_TEMPLATE_DIR = Path(__file__).parent / "templates"


class EvalReport:
    """Aggregates per-case results into a summary, regressions, and HTML."""

    def __init__(self, case_results: list[CaseResult]) -> None:
        self.case_results = case_results

    # ------------------------------------------------------------------ #
    def _metric_names(self) -> list[str]:
        names: list[str] = []
        for cr in self.case_results:
            for name in cr.scores:
                if name not in names:
                    names.append(name)
        return names

    def summary(self) -> dict[str, float]:
        """Mean of each metric across scored cases, plus cost/counts."""
        summary: dict[str, float] = {}
        for name in self._metric_names():
            values = [cr.scores[name] for cr in self.case_results if name in cr.scores]
            if values:
                summary[name] = sum(values) / len(values)
        total_cost = sum(
            cr.result.cost.total_cost for cr in self.case_results if cr.result is not None
        )
        summary["_total_cost_usd"] = total_cost
        summary["_cases"] = float(len(self.case_results))
        summary["_failures"] = float(sum(1 for cr in self.case_results if cr.failed))
        return summary

    def regressions(
        self, baseline: EvalReport | dict[str, float], *, threshold: float = 0.05
    ) -> dict[str, float]:
        """Flag metrics whose mean dropped more than ``threshold`` vs. baseline.

        Returns ``{metric: delta}`` where delta is negative (baseline - current
        > threshold). Only real metrics (not the ``_``-prefixed meta keys).
        """
        base = baseline.summary() if isinstance(baseline, EvalReport) else baseline
        current = self.summary()
        flagged: dict[str, float] = {}
        for name, base_value in base.items():
            if name.startswith("_"):
                continue
            cur_value = current.get(name)
            if cur_value is None:
                continue
            delta = cur_value - base_value
            if delta < -threshold:
                flagged[name] = delta
        if flagged:
            _logger.warning("regressions detected: %s", flagged)
        return flagged

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "cases": [
                {
                    "question": cr.case.question,
                    "scores": cr.scores,
                    "cost_usd": (cr.result.cost.total_cost if cr.result else None),
                    "error": cr.error,
                }
                for cr in self.case_results
            ],
        }

    def to_html(self, *, baseline: EvalReport | dict[str, float] | None = None) -> str:
        """Render a self-contained HTML report (no external assets)."""
        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(["html"]),
        )
        template = env.get_template("report.html.j2")
        regressions = self.regressions(baseline) if baseline is not None else {}
        return template.render(
            summary=self.summary(),
            metric_names=self._metric_names(),
            case_results=self.case_results,
            regressions=regressions,
        )
