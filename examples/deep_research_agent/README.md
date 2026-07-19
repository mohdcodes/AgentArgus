# Deep-Research Agent — AgentArgus end-to-end smoke test

One runnable script that exercises **every** AgentArgus capability in a single
run, printing a line per capability so you can see the whole library work
together.

```bash
uv pip install -e ".[dev,examples]"
uv run python examples/deep_research_agent/run.py
```

Set `ANTHROPIC_API_KEY` (in `.env`) for real answers/scores; without it a
`MockJudge` is used so it runs anywhere (answers/scores synthetic).

## What it exercises
A `SupervisorAgent` routes **retrieval → analysis → synthesis** (handoff chain),
the whole thing wrapped by an `Agent` with a tracer + cost tracker, and then a
tiny dataset is scored with RAG + agent metrics into an HTML report.

| Line | Capability |
|------|-----------|
| M1 wrap | any agent wrapped → `RunResult` |
| M2 trace | trace_id + spans |
| M3 cost | per-step cost ledger |
| M4 reliability | flaky tool fails 2×, retry recovers (visible in `errors`) |
| M5 RAG eval | faithfulness / answer_relevance / context_precision |
| M6 report | `report.html` + regression check |
| M7 agent eval | tool_use_accuracy / tool_success_rate / error_recovery_rate |
| M8 orchestrate | supervisor + handoff + SQLite checkpoint |
| M9 HITL | approval gate; a rejection is a controlled failure in `errors` |

## Honest notes
- `context_precision` shows `0.0` in the default run: the supervisor keeps
  retrieved contexts *inside* the handoff, so they don't reach the top-level
  scoring view — ContextPrecision correctly reports "no contexts to judge"
  rather than a fake number. Feed contexts into the case/metadata to score it.
- `tool_use_accuracy` reflects the labeled `expected_tools` per case; it needs
  those labels or it reports NOT_APPLICABLE.
- With no key, scores are synthetic (MockJudge). Use a real key for real signal.
- Traces are on `result.spans`; run Jaeger + `Tracer(exporter="otlp")` to see
  them in a UI (see `examples/README.md`).
