"""AgentArgus — evaluation, observability, and reliability for LLM agents.

The public API surface is intentionally small (spec Appendix A). Module 0
exposes the core result object, configuration, and the logging factory; later
modules add ``Agent``, ``EvalSuite``, ``EvalRunner``, ``ReliabilityPolicy``,
and the metrics.
"""

from __future__ import annotations

from agentargus._internal.exceptions import (
    AgentArgusError,
    CircuitOpenError,
    ConfigError,
    CostCeilingExceeded,
    SerializationError,
    TransientError,
)
from agentargus.agents import (
    Agent,
    BaseAgent,
    Handoff,
    LLMRouter,
    Recorder,
    SqliteCheckpointer,
    SupervisorAgent,
    record_step,
    record_tool_call,
)
from agentargus.config import AgentArgusConfig, Embedder, Judge, batch_complete
from agentargus.core import (
    CostBreakdown,
    ErrorRecord,
    RunResult,
    Span,
    Step,
    ToolCall,
)
from agentargus.eval import (
    AnswerRelevance,
    ContextPrecision,
    ContextRecall,
    ErrorRecoveryRate,
    EvalCase,
    EvalDataset,
    EvalReport,
    EvalRunner,
    EvalSuite,
    Faithfulness,
    Metric,
    PlanCoherence,
    ToolSuccessRate,
    ToolUseAccuracy,
)
from agentargus.logging import configure_logging, get_logger
from agentargus.observability import CostTracker, Tracer, Usage
from agentargus.reliability import (
    CircuitBreaker,
    DeadLetterQueue,
    FallbackChain,
    JsonlDeadLetterSink,
    ReliabilityPolicy,
    RetryWithBackoff,
)

__version__ = "0.1.0.dev0"

__all__ = [
    "__version__",
    "Agent",
    "BaseAgent",
    "Tracer",
    "CostTracker",
    "Usage",
    "ReliabilityPolicy",
    "RetryWithBackoff",
    "FallbackChain",
    "CircuitBreaker",
    "DeadLetterQueue",
    "JsonlDeadLetterSink",
    "RunResult",
    "Span",
    "ToolCall",
    "Step",
    "ErrorRecord",
    "CostBreakdown",
    "AgentArgusConfig",
    "Judge",
    "Embedder",
    "batch_complete",
    "Metric",
    "EvalSuite",
    "EvalDataset",
    "EvalCase",
    "EvalRunner",
    "EvalReport",
    "Faithfulness",
    "AnswerRelevance",
    "ContextPrecision",
    "ContextRecall",
    "ToolUseAccuracy",
    "ToolSuccessRate",
    "ErrorRecoveryRate",
    "PlanCoherence",
    "Recorder",
    "record_tool_call",
    "record_step",
    "SupervisorAgent",
    "Handoff",
    "LLMRouter",
    "SqliteCheckpointer",
    "AgentArgusError",
    "ConfigError",
    "SerializationError",
    "CostCeilingExceeded",
    "TransientError",
    "CircuitOpenError",
    "get_logger",
    "configure_logging",
]
