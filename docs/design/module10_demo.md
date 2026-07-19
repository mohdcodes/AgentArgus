# Module 10 — Deep-Research Demo (end-to-end): Design

> The full-stack validation: one demo exercising all 9 capabilities. Matches the
> spec's Module 10 + the v0.1.0 Definition of Done (§12).

## Goal
A `examples/deep_research_agent/` that runs end-to-end and demonstrably uses:
wrap (M1), trace (M2), cost (M3), reliability recovering an injected failure
(M4), RAG metrics (M5), dataset + runner + HTML report (M6), agent metrics (M7),
supervisor + handoff + SQLite checkpoint (M8), HITL approval (M9).

## Decisions locked
1. **Task:** a `SupervisorAgent` routing across 3 workers — **retrieval →
   analysis → synthesis** (handoff chain) — answering research questions.
2. **LLM:** real `AnthropicJudge` if `ANTHROPIC_API_KEY` set, else `MockJudge`
   (reuses `examples/_shared`). Prints which mode it's in.
3. **Injected failure:** the retrieval worker's tool raises `TransientError` on
   its first 2 calls, then succeeds — wrapped in `ReliabilityPolicy(retry=3)`.
   `RunResult.errors` shows 2 recovered failures.
4. **Artifacts:** (a) console capability checklist, (b) cost ledger + span-tree
   dump, (c) `report.html` eval report over a small dataset (RAG + agent metrics).

## Structure
```
examples/deep_research_agent/
├── README.md            # what it shows, how to run (key optional), Jaeger note
├── workers.py           # retrieval (flaky tool), analysis, synthesis workers
├── dataset.jsonl        # ~4 research questions + expected_tools + reference
└── run.py               # wires everything, runs, prints artifacts, writes report.html
```

## The pipeline (one coherent run)
```
question
  -> SupervisorAgent(router, checkpointer=SqliteCheckpointer, tracer, dead_letter)
       route -> retrieval worker
                  HITL: Checkpoint("expensive_crawl").require_approval(...)   # M9
                  flaky search tool (fails 2x, retry recovers)               # M4
                  record_tool_call(...) ; Handoff -> analysis                # M7/M8
       analysis worker: record_step reasoning ; Handoff -> synthesis
       synthesis worker: LLM composes final answer (grounded in retrieved ctx)
  -> wrapped in Agent(tracer=Tracer, cost=CostTracker)                       # M1/M2/M3
  -> RunResult (output, trace_id, spans, cost, tool_calls, steps, errors)
```
Then over a small `dataset.jsonl`:
```
EvalRunner.run(agent, dataset, EvalSuite([
    Faithfulness, AnswerRelevance, ContextPrecision,   # RAG (M5)
    ToolUseAccuracy, ToolSuccessRate, ErrorRecoveryRate # agent (M7)
])) -> EvalReport -> report.html + regressions          # M6
```

## Capability checklist the demo prints (proof each fired)
```
[M1 wrap]         agent wrapped, RunResult produced          ✓
[M2 trace]        trace_id=... , N spans                     ✓
[M3 cost]         $X.XXXX , per-step ledger printed          ✓
[M4 reliability]  2 transient failures recovered (retry)     ✓
[M5 RAG eval]     faithfulness=.. relevance=.. precision=..  ✓
[M6 report]       report.html written, regressions checked   ✓
[M7 agent eval]   tool_use_accuracy=.. success=.. recovery=..✓
[M8 orchestrate]  retrieval->analysis->synthesis, checkpointed ✓
[M9 HITL]         approval gate hit, decision=approved        ✓
```

## Honesty / degradation
- No key → MockJudge; the demo still runs and every capability still fires
  (scores synthetic). Clearly labelled.
- Jaeger optional: `Tracer()` in-memory by default; a `--otlp` flag (or env)
  switches to OTLP if a collector is running. Spans are on `RunResult.spans`
  regardless, and the demo dumps the span tree to console.
- Real spend when keyed — small dataset (~4 cases) keeps it to cents; documented.

## Verification (I run with Mock; owner runs with key)
- Demo runs to completion under MockJudge; prints all 9 ✓ lines; writes a valid
  `report.html`; `RunResult.errors` shows the 2 recovered failures; the HITL
  decision is recorded. No new library code — pure composition of Modules 0–9.

## Not building new library code
Module 10 is an **example/demo**, not library surface. If it reveals a gap in
the library, that's a finding to fix in the relevant module — flagged, not
patched inside the demo.
