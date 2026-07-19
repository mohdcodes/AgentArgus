"""Run the multi-tool agent through AgentArgus and print real results.

    python examples/tool_agent/run.py

Each case carries an ``expected_tools`` label so ToolUseAccuracy can score
whether the agent picked the right tools; ToolSuccessRate scores whether the
calls it made succeeded. Set ANTHROPIC_API_KEY for real tool-planning.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agentargus import (  # noqa: E402
    Agent,
    CostTracker,
    EvalSuite,
    ToolSuccessRate,
    ToolUseAccuracy,
    Tracer,
)
from examples._shared import get_llm  # noqa: E402
from examples.tool_agent.agent import make_tool_agent  # noqa: E402

# Each case: the question + the tools a good run SHOULD call (the ground truth
# ToolUseAccuracy needs — like a reference answer).
CASES = [
    {"question": "What is 1234 * 5678?", "expected_tools": ["calculator"]},
    {
        "question": "Search for the latest on quantum computing and tell me the time.",
        "expected_tools": ["web_search", "current_time"],
    },
    {
        "question": "What is (100 + 50) / 3, and search for context on the number?",
        "expected_tools": ["calculator", "web_search"],
    },
]


def main() -> None:
    llm = get_llm()
    agent_fn = make_tool_agent(llm)
    tracker = CostTracker(pricing={"claude-opus-4-8": (15.0, 75.0)})
    agent = Agent(agent_fn, tracer=Tracer(), cost=tracker, name="tool-agent")

    suite = EvalSuite([ToolUseAccuracy(), ToolSuccessRate()])

    for case in CASES:
        result = agent.run(case["question"])
        usage = getattr(llm, "last_usage", None)
        if usage:
            tracker.add_usage(usage, model="claude-opus-4-8", step="tool_agent")

        # Scoring view: metrics read expected_tools from metadata; actual tool
        # calls are already on result.tool_calls (from the recorder).
        view = replace(result, metadata={**result.metadata, **case})
        scores = suite.run(view)

        print("=" * 70)
        print(f"Q: {case['question']}")
        print(f"A: {result.output}")
        called = [t.name for t in result.tool_calls]
        print(f"   tools called : {called}")
        print(f"   expected     : {case['expected_tools']}")
        print(f"   scores       : { ({k: round(v, 3) for k, v in scores.items()}) }")

    print("=" * 70)
    print(f"TOTAL cost this run: ${tracker.total().total_cost:.5f}")


if __name__ == "__main__":
    main()
