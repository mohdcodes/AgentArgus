"""A multi-tool general agent for the AgentArgus example.

The agent has several tools (calculator, a mock web search, current-time). For a
given question it decides (via the LLM) which tools to call, records each call,
and returns an answer. AgentArgus then scores tool behaviour (ToolUseAccuracy vs.
an expected-tools label, ToolSuccessRate) and traces/costs the run.

The tools are simple/local so the example is self-contained and deterministic
where it can be; the point is exercising AgentArgus's agent metrics.
"""

from __future__ import annotations

import ast
import operator
from datetime import datetime, timezone

# --- the tools ------------------------------------------------------------- #
_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
}


def calculator(expression: str) -> float:
    """Safely evaluate a simple arithmetic expression (no eval())."""

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.BinOp):
            return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp):
            return _OPS[type(node.op)](_eval(node.operand))
        raise ValueError(f"unsupported expression: {expression!r}")

    return _eval(ast.parse(expression, mode="eval").body)


def web_search(query: str) -> list[str]:
    """A MOCK web search — returns canned snippets (no network)."""
    return [
        f"Result 1 about '{query}': a concise factual snippet.",
        f"Result 2 about '{query}': supporting detail.",
    ]


def current_time() -> str:
    """Return the current UTC time as an ISO string."""
    return datetime.now(timezone.utc).isoformat()


TOOLS = {"calculator": calculator, "web_search": web_search, "current_time": current_time}


def make_tool_agent(llm: object):
    """Build agent(question) -> answer that uses the LLM to pick + call tools.

    The LLM is asked which tools to use; the agent executes them, records each
    call, then asks the LLM to compose a final answer from the tool outputs.
    """
    import json

    from agentargus import record_step, record_tool_call

    def agent(question: str) -> str:
        # 1. Ask the LLM which tools to call (structured).
        plan_prompt = (
            "You have these tools: calculator(expression), web_search(query), "
            "current_time(). For the QUESTION, reply with JSON: "
            '{"calls": [{"tool": "...", "arg": "..."}]} listing the tools to '
            f"call in order.\n\nQUESTION: {question}"
        )
        record_step("reason", "planning which tools to use")
        raw = llm.complete(plan_prompt)  # type: ignore[attr-defined]
        try:
            block = raw[raw.index("{") : raw.rindex("}") + 1]
            calls = json.loads(block).get("calls", [])
        except (ValueError, json.JSONDecodeError):
            calls = []

        # 2. Execute each planned tool call, recording success/failure.
        outputs: list[str] = []
        for call in calls:
            tool = call.get("tool")
            arg = call.get("arg")
            fn = TOOLS.get(tool)
            if fn is None:
                record_tool_call(
                    tool or "unknown", {"arg": arg}, None, success=False, error="unknown tool"
                )
                continue
            try:
                result = fn() if tool == "current_time" else fn(arg)
                record_tool_call(tool, {"arg": arg}, result, success=True)
                outputs.append(f"{tool}({arg}) -> {result}")
            except Exception as exc:  # noqa: BLE001 - record tool failure, continue
                record_tool_call(tool, {"arg": arg}, None, success=False, error=str(exc))

        # 3. Compose the final answer from tool outputs.
        answer_prompt = (
            "Using these TOOL RESULTS, answer the QUESTION concisely.\n\n"
            f"TOOL RESULTS:\n{chr(10).join(outputs) or '(none)'}\n\nQUESTION: {question}"
        )
        answer = llm.complete(answer_prompt)  # type: ignore[attr-defined]
        record_step("answer", answer)
        return answer

    agent.__name__ = "tool_agent"
    return agent
