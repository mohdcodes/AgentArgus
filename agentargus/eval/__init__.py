"""Evaluation: metrics, suite (and, in later modules, dataset/runner/report)."""

from agentargus.eval.dataset import EvalCase, EvalDataset
from agentargus.eval.metrics import (
    AnswerRelevance,
    ContextPrecision,
    ContextRecall,
    ErrorRecoveryRate,
    Faithfulness,
    LLMJudgeMetric,
    Metric,
    PlanCoherence,
    ToolSuccessRate,
    ToolUseAccuracy,
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
    "ToolUseAccuracy",
    "ToolSuccessRate",
    "ErrorRecoveryRate",
    "PlanCoherence",
    "EvalCase",
    "EvalDataset",
    "EvalRunner",
    "CaseResult",
    "EvalReport",
]
