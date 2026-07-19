"""GenAI semantic-convention attribute keys — the single source of truth.

Every span-attribute write across AgentArgus imports its key from here, so
there are no magic strings scattered across modules (a reuse point per spec
§6.2). These follow the OpenTelemetry GenAI semantic conventions.

NOTE: the OTel GenAI conventions are still evolving. The subset defined here is
what Module 1 needs; Module 2 (Tracer) extends this list. Keys are kept as plain
constants so a convention rename is a one-line change here, not a scatter-gun
edit. **VERIFY against the current OTel spec before adding new keys.**
"""

from __future__ import annotations

__all__ = [
    "GEN_AI_SYSTEM",
    "GEN_AI_OPERATION_NAME",
    "GEN_AI_REQUEST_MODEL",
    "GEN_AI_USAGE_INPUT_TOKENS",
    "GEN_AI_USAGE_OUTPUT_TOKENS",
]

GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
