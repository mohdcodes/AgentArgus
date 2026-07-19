"""Metric abstraction (spec §6.5).

``Metric`` is the ABC every evaluation metric implements; ``EvalSuite`` iterates
``list[Metric]`` polymorphically. ``compute`` is **methodoverload site #4**: it
accepts either a full ``RunResult`` (production) or a lightweight ``dict``
(unit tests), normalising both to a ``MetricInput`` before scoring — so the
scoring logic lives in one place, not duplicated per input shape.
"""

# NOTE: NO ``from __future__ import annotations`` — methodoverload dispatches on
# runtime annotations via isinstance, and PEP 563 stringization breaks that (see
# docs/concepts/methodoverload.md). The dict overload uses bare ``dict``.

import json
import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from methodoverload import overload

from agentargus.config import Judge
from agentargus.core import ErrorRecord, RunResult, Step, ToolCall
from agentargus.logging import get_logger

__all__ = ["Metric", "MetricInput", "LLMJudgeMetric", "NOT_APPLICABLE", "cosine_similarity"]

_logger = get_logger("eval.metrics")

# Sentinel a metric returns when it cannot be computed for a given input (e.g.
# ContextRecall with no ground-truth reference). EvalSuite excludes these from
# the scores dict rather than recording a misleading number.
NOT_APPLICABLE = float("nan")


@dataclass(frozen=True, slots=True)
class MetricInput:
    """Normalised inputs a metric needs, from a RunResult or a test dict.

    RAG fields (Module 5) plus agent-behaviour fields (Module 7). Agent metrics
    read tool_calls/steps/errors; ``expected_tools`` is the author-supplied
    ground truth for ToolUseAccuracy (empty ⇒ not applicable).
    """

    question: str
    answer: str
    contexts: tuple[str, ...] = ()
    reference: str | None = None  # ground truth, for ContextRecall
    # --- agent-behaviour fields (Module 7) --- #
    tool_calls: tuple[ToolCall, ...] = ()
    steps: tuple[Step, ...] = ()
    errors: tuple[ErrorRecord, ...] = ()
    expected_tools: tuple[str, ...] = ()  # ground truth, for ToolUseAccuracy


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity (no numpy dep)."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class Metric(ABC):
    """Base class for all evaluation metrics. Concrete metrics implement ``_score``."""

    name: str = "metric"

    @overload
    def compute(self, source: RunResult) -> float:
        """Score a production ``RunResult``."""
        return self._score(self._from_run_result(source))

    @overload  # type: ignore[no-redef]  # methodoverload merges runtime overloads; mypy sees a redefinition
    def compute(self, source: dict) -> float:  # type: ignore[type-arg]  # noqa: F811  # bare `dict` REQUIRED for isinstance dispatch (no subscripted generics)
        """Score a lightweight test ``dict`` with question/answer/contexts keys."""
        return self._score(self._from_dict(source))

    # -- extraction (shared) ------------------------------------------------ #
    @staticmethod
    def _from_run_result(result: RunResult) -> MetricInput:
        md = result.metadata
        return MetricInput(
            question=str(md.get("question", "")),
            answer=str(result.output),
            contexts=tuple(md.get("contexts", ()) or ()),
            reference=md.get("reference"),
            tool_calls=tuple(result.tool_calls),
            steps=tuple(result.steps),
            errors=tuple(result.errors),
            expected_tools=tuple(md.get("expected_tools", ()) or ()),
        )

    @staticmethod
    def _from_dict(source: dict[str, Any]) -> MetricInput:
        return MetricInput(
            question=str(source.get("question", "")),
            answer=str(source.get("answer", "")),
            contexts=tuple(source.get("contexts", ()) or ()),
            reference=source.get("reference"),
            tool_calls=tuple(source.get("tool_calls", ()) or ()),
            steps=tuple(source.get("steps", ()) or ()),
            errors=tuple(source.get("errors", ()) or ()),
            expected_tools=tuple(source.get("expected_tools", ()) or ()),
        )

    @abstractmethod
    def _score(self, inp: MetricInput) -> float:
        """Compute the metric from normalised inputs."""
        raise NotImplementedError


class LLMJudgeMetric(Metric):
    """Base for LLM-judge metrics — holds the injected judge and JSON parsing.

    Methodology for the concrete RAG metrics is modeled on RAGAS (Apache-2.0),
    which AgentArgus credits but does not depend on (spec §1/§9: single-package,
    minimal deps).
    """

    def __init__(self, judge: Judge | None = None) -> None:
        self._judge = judge

    def _require_judge(self) -> Judge:
        if self._judge is None:
            raise ValueError(
                f"{type(self).__name__} needs a judge to compute. "
                f"Construct it with a Judge: {type(self).__name__}(judge=my_judge)."
            )
        return self._judge

    def _ask_json(self, prompt: str, *, default: dict[str, Any]) -> dict[str, Any]:
        """Call the judge and parse JSON, tolerantly (never crash the batch)."""
        text = self._require_judge().complete(prompt)
        # Strip a ```json ... ``` fence if present.
        fenced = re.search(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", text, re.DOTALL)
        candidate = fenced.group(1) if fenced else text
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            pass
        # Lenient: try to find the first {...} block.
        block = re.search(r"\{.*\}", text, re.DOTALL)
        if block:
            try:
                parsed = json.loads(block.group(0))
                if isinstance(parsed, dict):
                    return parsed
            except (ValueError, TypeError):
                pass
        _logger.warning(
            "%s: could not parse judge JSON; using conservative default.",
            type(self).__name__,
        )
        return default
