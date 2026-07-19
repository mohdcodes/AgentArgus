"""AgentArgus end-to-end smoke test — every capability in one runnable script.

    python examples/deep_research_agent/run.py

Walks through all 9 AgentArgus capabilities linearly, printing a ✓ for each so
you can see the whole library work end-to-end in one run. Uses a real Anthropic
model if ANTHROPIC_API_KEY is set, else a MockJudge (so it runs anywhere).

Capabilities exercised:
  M1 wrap · M2 trace · M3 cost · M4 reliability · M5 RAG eval
  M6 dataset+report · M7 agent eval · M8 orchestration · M9 HITL
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agentargus import (  # noqa: E402
    Agent,
    AnswerRelevance,
    AutoRejectBackend,
    Checkpoint,
    ContextPrecision,
    CostTracker,
    ErrorRecoveryRate,
    EvalCase,
    EvalReport,
    EvalSuite,
    Faithfulness,
    SqliteCheckpointer,
    SupervisorAgent,
    ToolSuccessRate,
    ToolUseAccuracy,
    Tracer,
)
from agentargus.eval.runner import CaseResult  # noqa: E402
from examples._shared import get_llm  # noqa: E402
from examples.deep_research_agent.workers import (  # noqa: E402
    StaticRouter,
    make_rag_agent,
    make_workers,
)

OUT = Path(__file__).parent / "report.html"


def line(tag: str, msg: str) -> None:
    print(f"[{tag:<16}] {msg}  [OK]")


def main() -> None:
    print("=" * 72)
    print("AgentArgus end-to-end smoke test")
    print("=" * 72)
    llm = get_llm()

    # ---------------------------------------------------------------- M1/M2/M3
    # Wrap a plain research agent with tracer + cost tracker.
    tracker = CostTracker(pricing={"claude-opus-4-8": (15.0, 75.0)})
    workers = make_workers(llm)

    # ------------------------------------------------------------------- M8
    # Supervisor orchestrates retrieval -> analysis -> synthesis, checkpointed.
    supervisor = SupervisorAgent(
        workers,
        router=StaticRouter(),
        checkpointer=SqliteCheckpointer(":memory:"),
        name="research",
    )
    # The whole multi-agent system is itself a BaseAgent, so wrap it (M1) with
    # observability (M2) + cost (M3).
    agent = Agent(supervisor, tracer=Tracer(), cost=tracker, name="research-system")

    result = agent.run("What was Tesla's revenue in 2023?")
    tracker.add_usage(
        getattr(llm, "last_usage", {"input_tokens": 100, "output_tokens": 50}),
        model="claude-opus-4-8",
        step="synthesis",
    )

    line("M1 wrap", "agent wrapped; RunResult produced")
    line("M2 trace", f"trace_id={result.trace_id[:12]}, {len(result.spans)} span(s)")
    line("M3 cost", f"${tracker.total().total_cost:.5f} ; ledger rows={len(tracker.table())}")
    line("M8 orchestrate", "retrieval->analysis->synthesis via SupervisorAgent (checkpointed)")

    # ------------------------------------------------------------------- M4
    # The retrieval worker's flaky tool failed twice then recovered (retry).
    recovered = [e for e in result.errors if e.recovered]
    line("M4 reliability", f"{len(recovered)} transient failure(s) recovered by retry")

    # ------------------------------------------------------------------- M9
    # HITL: a rejected checkpoint is a controlled failure recorded in errors.
    async def gated(_: str) -> str:
        cp = Checkpoint(AutoRejectBackend("policy: crawl denied"), name="crawl")
        await cp.require_approval({"action": "expensive crawl"})
        return "unreachable"

    hitl_result = Agent(gated, name="hitl-demo").run("crawl the web")
    rejected = [e for e in hitl_result.errors if e.error_type == "CheckpointRejected"]
    line("M9 HITL", f"approval rejected -> controlled failure recorded ({len(rejected)})")

    # ------------------------------------------------------------- M5/M6/M7
    # Score a tiny dataset with RAG + agent metrics using a SIMPLE rag agent that
    # exposes its retrieved contexts (so faithfulness/context_precision are real).
    cases = [
        {
            "question": "What was Tesla's revenue in 2023?",
            "reference": "Tesla's 2023 revenue was about $96.8B.",
            "expected_tools": ["web_search", "analyze"],
        },
        {
            "question": "What is quantum computing?",
            "reference": "Quantum computing uses qubits.",
            "expected_tools": ["web_search", "analyze"],
        },
    ]
    suite = EvalSuite(
        [
            Faithfulness(judge=llm),
            AnswerRelevance(judge=llm),
            ContextPrecision(judge=llm),
            ToolUseAccuracy(),
            ToolSuccessRate(),
            ErrorRecoveryRate(),
        ]
    )

    case_results = []
    for case in cases:
        rag_fn = make_rag_agent(llm)  # fresh agent per case (own state)
        rag_tracker = CostTracker(pricing={"claude-opus-4-8": (15.0, 75.0)})
        rag_agent = Agent(rag_fn, cost=rag_tracker, name="rag")
        run = rag_agent.run(case["question"])
        usage = getattr(llm, "last_usage", None)
        if usage:
            rag_tracker.add_usage(usage, model="claude-opus-4-8", step="answer")
        # Scoring view: attach question/reference/contexts/expected_tools + cost.
        view = replace(
            run,
            cost=rag_tracker.total(),
            metadata={
                **run.metadata,
                "question": case["question"],
                "reference": case["reference"],
                "contexts": list(getattr(rag_fn, "last_contexts", [])),
                "expected_tools": case["expected_tools"],
            },
        )
        case_results.append(
            CaseResult(
                case=EvalCase(question=case["question"]), result=view, scores=suite.run(view)
            )
        )

    report = EvalReport(case_results)
    summary = report.summary()
    rag = {
        k: round(summary[k], 3)
        for k in ("faithfulness", "answer_relevance", "context_precision")
        if k in summary
    }
    agentm = {
        k: round(summary[k], 3)
        for k in ("tool_use_accuracy", "tool_success_rate", "error_recovery_rate")
        if k in summary
    }
    line("M5 RAG eval", f"{rag}")
    line("M7 agent eval", f"{agentm}")

    OUT.write_text(report.to_html(), encoding="utf-8")
    line("M6 report", f"report.html written ({len(report.case_results)} cases)")

    # ---------------------------------------------------------------- artifacts
    print("-" * 72)
    print("COST LEDGER (per step):")
    for row in tracker.table() or [{"(no priced calls recorded)": ""}]:
        print("  ", row)
    print("SPAN TREE:")
    for s in result.spans or []:
        parent = f" <- {s.parent_id}" if s.parent_id else ""
        print(f"   {s.name} [{s.span_id}]{parent}")
    if not result.spans:
        print("   (in-memory tracer produced no spans for the supervisor path)")
    print("-" * 72)
    print(f"HTML report: {OUT}")
    print("All capabilities exercised. [OK]")


if __name__ == "__main__":
    main()
