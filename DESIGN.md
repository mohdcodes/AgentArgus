# AgentArgus — Design, Scope, Non-Goals

> Owner-authored document. Claude Code seeds it from spec §1; the owner refines
> and defends each point.

## Problem
Teams ship LLM agents to production with almost no operational rigour. There is
no single place to answer: *Did the agent do the right thing? What did it cost?
Why did it fail? Can it recover?* RAGAS covers RAG eval only; observability
tools cover traces only; reliability is hand-rolled per project. AgentArgus is a
framework-agnostic, single-package answer.

## What AgentArgus is
A production-grade Python library that wraps *any* agent and uniformly adds
evaluation, observability, reliability, human-in-the-loop, and orchestration
helpers, producing a rich `RunResult` that evaluation consumes.

## The core design bet (checkpoint 1.1)
**Framework-agnostic wrapping.** We wrap a callable / LangGraph graph / BaseAgent
behind one `Agent` facade. The cost of that generality: we cannot piggyback any
one framework's trace schema, so we define our own (`RunResult` + GenAI-convention
spans), and we forgo framework-specific optimizations. The payoff: one API works
everywhere and nothing is locked to a vendor.

## Non-goals (defend these)
- **Not a model-serving / inference engine** — that's vLLM/TGI's job.
- **Not a vector DB or a RAG framework** — AgentArgus *evaluates* RAG; it does
  not *do* retrieval for you.
- **Not tied to any one agent framework** — LangGraph is *supported*, never
  *required*.
- **Not a hosted product in v0.1.0** — it is a library. A dashboard/app is a
  *consumer* of it.

## Key decisions log
See [DESIGN_LOG.md](DESIGN_LOG.md) for the per-module decision record and
[HARD_QUESTIONS.md](HARD_QUESTIONS.md) for the review questions.
