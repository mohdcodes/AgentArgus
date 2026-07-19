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
from agentargus.agents import Agent, BaseAgent
from agentargus.config import AgentArgusConfig, Judge, batch_complete
from agentargus.core import (
    CostBreakdown,
    ErrorRecord,
    RunResult,
    Span,
    Step,
    ToolCall,
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
    "batch_complete",
    "AgentArgusError",
    "ConfigError",
    "SerializationError",
    "CostCeilingExceeded",
    "TransientError",
    "CircuitOpenError",
    "get_logger",
    "configure_logging",
]
