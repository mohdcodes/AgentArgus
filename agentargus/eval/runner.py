"""Eval runner (spec §6.5): batch an agent over a dataset and score each case.

Cases run concurrently under a semaphore cap (async-core payoff). For each case
we run the agent, merge the case's question/reference/contexts into a *scoring
view* so the metrics can read them, score with the suite, and collect a
``CaseResult``. A case that raises is captured (not fatal) so one bad case does
not sink the batch.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from agentargus.core import RunResult
from agentargus.logging import get_logger

if TYPE_CHECKING:
    from agentargus.agents.base import BaseAgent
    from agentargus.eval.dataset import EvalCase, EvalDataset
    from agentargus.eval.report import EvalReport
    from agentargus.eval.suite import EvalSuite

__all__ = ["CaseResult", "EvalRunner"]

_logger = get_logger("eval.runner")


@dataclass(frozen=True, slots=True)
class CaseResult:
    """The outcome of evaluating one case: its run, its scores, any failure."""

    case: EvalCase
    result: RunResult | None
    scores: dict[str, float] = field(default_factory=dict)
    error: str | None = None

    @property
    def failed(self) -> bool:
        return self.error is not None


class EvalRunner:
    """Runs an agent over a dataset and scores each case with a suite."""

    def __init__(self, concurrency: int = 8) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        self._concurrency = concurrency

    async def arun(self, agent: BaseAgent, dataset: EvalDataset, suite: EvalSuite) -> EvalReport:
        from agentargus.eval.report import EvalReport

        semaphore = asyncio.Semaphore(self._concurrency)

        async def run_case(case: EvalCase) -> CaseResult:
            async with semaphore:
                return await self._eval_one(agent, case, suite)

        results = await asyncio.gather(*(run_case(c) for c in dataset.cases))
        return EvalReport(list(results))

    def run(self, agent: BaseAgent, dataset: EvalDataset, suite: EvalSuite) -> EvalReport:
        """Synchronous driver over ``arun`` (refuses a running loop, like Agent.run)."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.arun(agent, dataset, suite))
        raise RuntimeError(
            "EvalRunner.run() cannot be called inside a running event loop; "
            "use 'await runner.arun(...)'."
        )

    async def _eval_one(self, agent: BaseAgent, case: EvalCase, suite: EvalSuite) -> CaseResult:
        try:
            result = await agent.arun(case.question)
            view = self._scoring_view(result, case)
            scores = suite.run(view)
            return CaseResult(case=case, result=result, scores=scores)
        except Exception as exc:  # noqa: BLE001 - one bad case must not sink the batch
            _logger.warning("eval case failed (%s): %s", type(exc).__name__, exc)
            return CaseResult(case=case, result=None, error=f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _scoring_view(result: RunResult, case: EvalCase) -> RunResult:
        """Merge case fields into metadata so metrics can read them.

        Agent-produced contexts win; the case's pre-set contexts fill in only if
        the agent produced none.
        """
        md: dict[str, Any] = dict(result.metadata)
        md["question"] = case.question
        if case.reference is not None:
            md["reference"] = case.reference
        if not md.get("contexts") and case.contexts:
            md["contexts"] = list(case.contexts)
        return replace(result, metadata=md)
