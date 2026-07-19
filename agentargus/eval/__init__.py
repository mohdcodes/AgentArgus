"""Evaluation: metrics, suite (and, in later modules, dataset/runner/report)."""

from agentargus.eval.dataset import EvalCase, EvalDataset
from agentargus.eval.metrics import (
    AnswerRelevance,
    ContextPrecision,
    ContextRecall,
    Faithfulness,
    LLMJudgeMetric,
    Metric,
)
from agentargus.eval.report import EvalReport
from agentargus.eval.runner import CaseResult, EvalRunner
from agentargus.eval.suite import EvalSuite

__all__ = [
    "Metric",
    "LLMJudgeMetric",
    "EvalSuite",
    "Faithfulness",
    "AnswerRelevance",
    "ContextPrecision",
    "ContextRecall",
    "EvalCase",
    "EvalDataset",
    "EvalRunner",
    "CaseResult",
    "EvalReport",
]
