# AgentArgus

**Evaluation, observability, and reliability for LLM agents — framework-agnostic, in one package.**

[![PyPI](https://img.shields.io/pypi/v/agentargus.svg)](https://pypi.org/project/agentargus/)
[![Python](https://img.shields.io/pypi/pyversions/agentargus.svg)](https://pypi.org/project/agentargus/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Wrap *any* agent — a plain function, an async function, a callable object, a
LangGraph graph, or your own `BaseAgent` — and get, uniformly:

- **Evaluation** — RAG metrics (faithfulness, answer relevance, context
  precision/recall — methodology modeled on RAGAS) **and** agent-behaviour
  metrics (tool-use accuracy, tool success, plan coherence, error-recovery),
  scored over datasets with an HTML report and regression detection.
- **Observability** — OpenTelemetry traces (GenAI semantic conventions) with
  logs correlated to every run by `trace_id`, plus accurate token/cost accounting.
- **Reliability** — retry with exponential backoff, model fallback chains, a
  circuit breaker, and a dead-letter queue.
- **Human-in-the-loop** — approval checkpoints that pause a run; a rejection is a
  controlled, recorded failure, not a crash.
- **Orchestration** — a production-grade supervisor/worker + handoff pattern with
  a durable (SQLite, WAL, resumable-across-restart) checkpointer.

> **Why it exists.** Teams ship LLM agents with almost no operational rigour.
> RAGAS covers RAG evaluation only; observability tools cover traces only;
> reliability is hand-rolled per project. AgentArgus is a single, framework-
> agnostic package that answers *did the agent do the right thing, what did it
> cost, why did it fail, and can it recover?*

---

## Install

```bash
pip install agentargus                 # minimal runtime (no LLM client bundled)
pip install "agentargus[otlp]"         # + OTLP exporter (send traces to Jaeger)
pip install "agentargus[examples]"     # + anthropic + pypdf, to run the examples
pip install "agentargus[dev]"          # + test/lint/type tooling
```

Python 3.10+. AgentArgus ships **no** LLM client — you inject one via a tiny
`Judge` protocol, so the base install stays dependency-light and tied to no
vendor.

---

## Quickstart

```python
from agentargus import Agent, Tracer, CostTracker

def my_agent(question: str) -> str:      # your existing agent — any callable
    return f"answer to: {question}"

agent = Agent(
    my_agent,
    tracer=Tracer(),                                          # OTel spans
    cost=CostTracker(pricing={"claude-opus-4-8": (15.0, 75.0)}),  # $/1M in, out
)

result = agent.run("What is quantum computing?")
print(result.output)        # the answer
print(result.trace_id)      # correlation id (spans + logs)
print(result.cost.total_cost)   # dollars spent
print(result.spans)         # structured execution spans
```

Everything an agent produces lands on one canonical object, **`RunResult`**:
`output`, `trace_id`, `spans`, `cost`, `tool_calls`, `steps`, `errors`,
`scores`, `metadata`.

---

## Core concepts

### 1. Wrap anything (`Agent` / `BaseAgent`)
`Agent(inner)` wraps a sync fn, async fn, callable object, or a `BaseAgent`.
Orchestration is **async-core, sync-wraps**: `run()` drives `arun()`, so
reliability/tracing/cost work identically on both paths. Every collaborator
(tracer, cost, reliability, HITL) is optional — a bare `Agent(inner)` just works.

### 2. Observability
```python
from agentargus import Tracer, record_tool_call, record_step

tracer = Tracer(exporter="otlp")   # "memory" (default) | "console" | "otlp"

def agent(q):
    record_step("reason", "deciding to search")
    docs = web_search(q)
    record_tool_call("web_search", {"q": q}, docs, success=True)
    ...
```
Spans follow the GenAI semantic conventions; the OTel trace id becomes the run's
`trace_id`, and every log line during the run carries it.

### 3. Cost tracking (you supply the prices)
```python
from agentargus import CostTracker
tracker = CostTracker(pricing={"claude-opus-4-8": (15.0, 75.0)}, ceiling_usd=5.0)
tracker.add_usage(response.usage, model="claude-opus-4-8", step="synthesize")
tracker.total()      # aggregate CostBreakdown
tracker.table()      # per-step ledger: which step, tokens, $
```
Prices are **per 1M tokens**, user-supplied (no stale baked-in tables). Token
counts come from the provider's reported usage. A cost ceiling raises
`CostCeilingExceeded`.

### 4. Reliability
```python
from agentargus import Agent, ReliabilityPolicy, RetryWithBackoff, FallbackChain, CircuitBreaker, JsonlDeadLetterSink

agent = Agent(inner, reliability=ReliabilityPolicy(
    retry=RetryWithBackoff(max_attempts=3),   # exponential backoff + jitter
    fallbacks=[backup_agent],                  # try next on failure
    breaker=CircuitBreaker(failure_threshold=5),
    dead_letter=JsonlDeadLetterSink("dlq.jsonl"),
))
```
Composed **breaker → fallback → retry**. Every attempt is recorded on
`RunResult.errors` with a `recovered` flag. Only transient errors are retried by
default (not programming bugs).

### 5. Evaluation — RAG + agent metrics
```python
from agentargus import EvalSuite, Faithfulness, ToolUseAccuracy, ToolSuccessRate, ErrorRecoveryRate

suite = EvalSuite([
    Faithfulness(judge=my_judge),   # RAG (LLM-judge)
    ToolUseAccuracy(),              # did it call the RIGHT tools? (needs a label)
    ToolSuccessRate(),              # did the tools work?
    ErrorRecoveryRate(),            # did it recover from failures?
])
scored = suite.score(result)        # -> new RunResult with .scores
```
`my_judge` is any object with `.complete(prompt) -> str` (inject your Claude /
OpenAI / local client). RAG metric methodology is modeled on
[RAGAS](https://github.com/explodinggradients/ragas) (Apache-2.0) — implemented
independently, no `ragas` dependency.

### 6. Batch eval + HTML report
```python
from agentargus import EvalRunner, EvalDataset

dataset = EvalDataset.from_jsonl("cases.jsonl")
report  = EvalRunner().run(agent, dataset, suite)      # concurrent, capped
report.summary()                       # per-metric means, cost, failures
report.regressions(baseline=last_run)  # {"faithfulness": -0.08} if it dropped
open("report.html", "w").write(report.to_html())       # self-contained, shareable
```

### 7. Multi-agent orchestration
```python
from agentargus import SupervisorAgent, Handoff, SqliteCheckpointer

def retrieval(q):  return Handoff(target="synthesis", input=docs)  # hand off
def synthesis(x):  return final_answer

supervisor = SupervisorAgent(
    {"retrieval": Agent(retrieval), "synthesis": Agent(synthesis)},
    router=my_router,
    checkpointer=SqliteCheckpointer("runs.db"),   # resumable across a restart
)
# A supervisor IS a BaseAgent, so wrap the whole system:
result = Agent(supervisor, tracer=Tracer()).run("complex question")
```
Production-hardened: WAL + per-step status for crash-safe resume, per-hop spans,
`max_steps` + context-size guards, graceful partial-failure.

### 8. Human-in-the-loop
```python
from agentargus import Checkpoint, CallbackApprovalBackend

async def agent(q):
    cp = Checkpoint(CallbackApprovalBackend(ask_slack), name="expensive_crawl")
    decision = await cp.require_approval({"action": "50-page crawl", "cost": 2.50})
    query = decision.edited_input or q      # human can redirect
    return do_crawl(query)
# On rejection: result.output is None, result.metadata["failed"] is True,
#               result.errors[0].reason == the reason (no crash).
```

---

## Examples

Runnable agents wrapped with AgentArgus (see [`examples/`](examples/)):

- **`examples/resume_rag/`** — RAG over a resume; scored with RAG metrics.
- **`examples/tool_agent/`** — a multi-tool agent; scored with tool metrics.
- **`examples/deep_research_agent/`** — one end-to-end script exercising **all**
  capabilities (supervisor + handoff + reliability + HITL + cost + eval + HTML
  report) with a per-capability checklist.

```bash
pip install "agentargus[examples]"
cp .env.example .env          # add your ANTHROPIC_API_KEY (gitignored)
python examples/deep_research_agent/run.py
```
Without a key the examples run with a mock LLM (synthetic answers) so they work
anywhere; with a key you get real answers, scores, and cost.

## Seeing traces in Jaeger

```bash
docker run -d -p 16686:16686 -p 4318:4318 jaegertracing/all-in-one
# use Tracer(exporter="otlp"); open http://localhost:16686 and search the trace_id
```
Spans are always on `result.spans` too — Jaeger is the optional visual layer.

---

## Public API

```python
from agentargus import (
    Agent, BaseAgent,                                   # wrap
    Tracer, CostTracker, Usage,                         # observability + cost
    ReliabilityPolicy, RetryWithBackoff, FallbackChain,
    CircuitBreaker, DeadLetterQueue, JsonlDeadLetterSink,   # reliability
    Metric, EvalSuite, EvalDataset, EvalRunner, EvalReport, # eval
    Faithfulness, AnswerRelevance, ContextPrecision, ContextRecall,
    ToolUseAccuracy, ToolSuccessRate, ErrorRecoveryRate, PlanCoherence,
    SupervisorAgent, Handoff, LLMRouter, SqliteCheckpointer,  # orchestration
    Checkpoint, Decision, CallbackApprovalBackend, ConsoleApprovalBackend,  # HITL
    Judge, Embedder, AgentArgusConfig,                  # seams / config
    RunResult, Span, ToolCall, Step, ErrorRecord, CostBreakdown,   # core
    record_tool_call, record_step, get_logger,
)
```

---

## Design notes

- **`RunResult` is the spine.** One immutable object every module produces onto
  and eval consumes from.
- **Seams, not dependencies.** LLMs (`Judge`), embeddings (`Embedder`), exporters,
  and sinks are injected protocols/ABCs — swap any backend, ship none in core.
- **The four OOP pillars** are used deliberately (abstraction via ABCs,
  encapsulation e.g. the circuit-breaker state machine, inheritance for metrics/
  strategies, polymorphism in `EvalSuite` and `SupervisorAgent`).
- Uses [`methodoverload`](https://pypi.org/project/methodoverload/) for
  type-dispatched methods where it genuinely reads cleaner.

More: [`docs/`](docs/), including [`docs/releasing.md`](docs/releasing.md).

---

## Contributing & issues

Bug reports and feature requests are welcome — please open a
[GitHub issue](https://github.com/mohdcodes/AgentArgus/issues) (templates
provided). See [CONTRIBUTING.md](CONTRIBUTING.md) for the dev setup (uv-based)
and the test/lint/type gate.

## Credits

RAG metric methodology follows [RAGAS](https://github.com/explodinggradients/ragas)
(Apache-2.0), implemented independently. Tracing uses
[OpenTelemetry](https://opentelemetry.io/).

## License

MIT © Mohd Arbaaz Siddiqui
