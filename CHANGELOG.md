# Changelog

All notable changes to AgentArgus are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-07-20

First public release.

### Added
- **Core** — `RunResult` (deeply-immutable canonical result) + value objects
  (`Span`, `ToolCall`, `Step`, `ErrorRecord`, `CostBreakdown`),
  `AgentArgusConfig`, colorized/JSON logging with `trace_id` correlation.
- **Agents** — `BaseAgent` contract and the `Agent` facade (async-core,
  sync-wraps); a run `Recorder` (`record_tool_call` / `record_step`).
- **Observability** — `Tracer` (OpenTelemetry, memory/console/OTLP exporters,
  GenAI semantic conventions) and `CostTracker` (user-supplied per-1M pricing,
  per-step ledger, cost ceiling).
- **Reliability** — `RetryWithBackoff`, `FallbackChain`, `CircuitBreaker`,
  `DeadLetterQueue`, composed by `ReliabilityPolicy`.
- **Evaluation** — `Metric` ABC + `EvalSuite`; RAG metrics (`Faithfulness`,
  `AnswerRelevance`, `ContextPrecision`, `ContextRecall`) modeled on RAGAS;
  agent metrics (`ToolUseAccuracy`, `ToolSuccessRate`, `ErrorRecoveryRate`,
  `PlanCoherence`); `EvalDataset`, `EvalRunner`, `EvalReport` (HTML + regressions).
- **Orchestration** — `SupervisorAgent` + `Handoff` + a durable
  `SqliteCheckpointer` (WAL, per-step status, resumable across a restart).
- **Human-in-the-loop** — `Checkpoint` with pluggable approval backends;
  rejection is a controlled failure recorded on `RunResult.errors`.
- **Examples** — resume RAG, multi-tool agent, and an end-to-end deep-research
  demo exercising every capability.
- Injectable `Judge` / `Embedder` protocols; no LLM client bundled in core.
