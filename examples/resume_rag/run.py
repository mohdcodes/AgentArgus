"""Run the resume-RAG example through AgentArgus and print real results.

    python examples/resume_rag/run.py

Set ANTHROPIC_API_KEY for real answers/scores; otherwise a MockJudge is used
(synthetic). Shows: the answer, cost, retrieved contexts, and RAG eval scores.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

# Allow running as a script: add repo root to the path.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agentargus import (  # noqa: E402
    Agent,
    AnswerRelevance,
    ContextPrecision,
    CostTracker,
    EvalSuite,
    Faithfulness,
    Tracer,
)
from examples._shared import get_llm  # noqa: E402
from examples.resume_rag.agent import make_resume_agent  # noqa: E402

QUESTIONS = [
    "Where does the candidate currently work and what is their role?",
    "What did the candidate build at Techdome?",
    "What open-source library has the candidate published?",
    "What is the candidate's GPA and where did they study?",
]


def main() -> None:
    llm = get_llm()
    agent_fn, _chunks = make_resume_agent(llm)

    tracker = CostTracker(pricing={"claude-opus-4-8": (15.0, 75.0)})
    agent = Agent(agent_fn, tracer=Tracer(), cost=tracker, name="resume-rag")

    suite = EvalSuite(
        [
            Faithfulness(judge=llm),
            AnswerRelevance(judge=llm),
            ContextPrecision(judge=llm),
        ]
    )

    for q in QUESTIONS:
        result = agent.run(q)
        # Charge the LLM usage the agent's llm recorded (real judge exposes it).
        usage = getattr(llm, "last_usage", None)
        if usage:
            tracker.add_usage(usage, model="claude-opus-4-8", step="answer")

        # Build a scoring view: metrics read question/contexts from metadata.
        view = replace(
            result,
            metadata={
                **result.metadata,
                "question": q,
                "contexts": list(getattr(agent_fn, "last_contexts", [])),
            },
        )
        scores = suite.run(view)

        print("=" * 70)
        print(f"Q: {q}")
        print(f"A: {result.output}")
        print(f"   trace_id={result.trace_id[:12]}  cost=${result.cost.total_cost:.5f}")
        print(f"   scores: { ({k: round(v, 3) for k, v in scores.items()}) }")

    print("=" * 70)
    print(f"TOTAL cost this run: ${tracker.total().total_cost:.5f}")


if __name__ == "__main__":
    main()
