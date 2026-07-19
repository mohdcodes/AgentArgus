# AgentArgus

Framework-agnostic **evaluation, observability, and reliability** for LLM agents — in one package.

Wrap any agent (a callable, a LangGraph graph, a `BaseAgent`) and get, uniformly:

- **Evaluation** — RAG metrics (faithfulness, answer relevance, context precision/recall) *and* agent metrics (tool-use accuracy, plan coherence, error-recovery), with regression detection.
- **Observability** — OpenTelemetry traces (GenAI semantic conventions) + accurate token/cost accounting.
- **Reliability** — retry/backoff, model fallback chains, circuit breaker, dead-letter queue.
- **Human-in-the-loop** — approval checkpoints that can pause a run.
- **Orchestration helpers** — supervisor/worker and agent handoff patterns.

> **Status:** early development (`0.1.0.dev0`). Built module-by-module per the [implementation spec](IMPLEMENTAION.md). See [DESIGN_LOG.md](DESIGN_LOG.md) for the decision record.

## Install

```bash
pip install agentargus            # minimal runtime
pip install "agentargus[dev]"     # + test/lint/judge-adapter tooling
```

## Design docs

- [DESIGN.md](DESIGN.md) — problem, scope, non-goals.
- [DESIGN_LOG.md](DESIGN_LOG.md) — per-module decision log.
- [HARD_QUESTIONS.md](HARD_QUESTIONS.md) — review questions per module.

## License

MIT © Mohd Arbaaz Siddiqui
