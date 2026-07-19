"""Agent-behaviour metrics (spec §6.5, Module 7).

These judge *how the agent behaved* (from tool_calls / steps / errors on the
RunResult), not the text quality. Three are deterministic (no LLM) — which is the
concrete proof that the ``Metric`` abstraction is not LLM-specific.

    ToolUseAccuracy   = F1(expected tool names, actual tool names)  [NEEDS a label]
    ToolSuccessRate   = successful tool calls / total               [any run]
    ErrorRecoveryRate = recovered errors / total errors             [any run]
    PlanCoherence     = LLM judge over ordered steps                [needs steps]
"""

from agentargus.eval.metrics.base import NOT_APPLICABLE, LLMJudgeMetric, Metric, MetricInput
from agentargus.logging import get_logger

__all__ = ["ToolUseAccuracy", "ToolSuccessRate", "ErrorRecoveryRate", "PlanCoherence"]

_logger = get_logger("eval.metrics.agent")


class ToolUseAccuracy(Metric):
    """Did the agent call the RIGHT tools? — F1(expected names, actual names).

    **Requires a ground-truth label.** "Correct tool use" is undefined without
    someone specifying what the correct tools are, so the dataset author MUST
    provide the expected tool names per case in
    ``metadata["expected_tools"]`` (analogous to a ``reference`` answer). With no
    expected list this metric returns ``NOT_APPLICABLE`` (excluded from the
    scores) — it never guesses which tool was "correct". Matching is over the
    SET of tool names (order- and argument-insensitive) in this version.
    """

    name = "tool_use_accuracy"

    def _score(self, inp: MetricInput) -> float:
        if not inp.expected_tools:
            _logger.info(
                "tool_use_accuracy: no metadata['expected_tools'] provided; "
                "not applicable. Label expected tools per case to score this."
            )
            return NOT_APPLICABLE
        expected = set(inp.expected_tools)
        actual = {tc.name for tc in inp.tool_calls}
        if not expected and not actual:
            return 1.0
        hits = len(expected & actual)
        if hits == 0:
            return 0.0
        precision = hits / len(actual) if actual else 0.0
        recall = hits / len(expected) if expected else 0.0
        return 2 * precision * recall / (precision + recall)


class ToolSuccessRate(Metric):
    """Did the tools the agent called actually succeed? — successes / total.

    Works on ANY run with zero setup (reads the ``success`` flag already on each
    recorded ``ToolCall``). NOT_APPLICABLE if the run made no tool calls.
    """

    name = "tool_success_rate"

    def _score(self, inp: MetricInput) -> float:
        if not inp.tool_calls:
            return NOT_APPLICABLE
        successes = sum(1 for tc in inp.tool_calls if tc.success)
        return successes / len(inp.tool_calls)


class ErrorRecoveryRate(Metric):
    """How well did the reliability layer recover? — recovered / total errors.

    Reads the ``recovered`` flag Module 4 sets on each ``ErrorRecord``. A run with
    no errors scores 1.0 (nothing to recover from = perfect).
    """

    name = "error_recovery_rate"

    def _score(self, inp: MetricInput) -> float:
        if not inp.errors:
            return 1.0
        recovered = sum(1 for e in inp.errors if e.recovered)
        return recovered / len(inp.errors)


class PlanCoherence(LLMJudgeMetric):
    """Is the agent's reasoning/action plan coherent? — LLM judge over steps.

    NOT_APPLICABLE if the run recorded no steps (can't judge a plan that wasn't
    recorded — distinct from judging it incoherent).
    """

    name = "plan_coherence"

    def _score(self, inp: MetricInput) -> float:
        if not inp.steps:
            _logger.info("plan_coherence: no steps recorded; not applicable.")
            return NOT_APPLICABLE
        plan = "\n".join(f"{s.index}. [{s.kind}] {s.content}" for s in inp.steps)
        prompt = (
            "Rate from 0.0 to 1.0 how coherent and well-ordered this agent's PLAN "
            "is for answering the QUESTION (1.0 = each step follows logically and "
            'advances the goal). Reply with JSON: {"coherence": 0.0-1.0}.\n\n'
            f"QUESTION:\n{inp.question}\n\nPLAN:\n{plan}"
        )
        data = self._ask_json(prompt, default={"coherence": 0.0})
        try:
            return max(0.0, min(1.0, float(data.get("coherence", 0.0))))
        except (TypeError, ValueError):
            return 0.0
