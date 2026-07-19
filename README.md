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

## Public API reference

Everything below is importable directly: `from agentargus import <name>`.

### Agents — wrap & run

| Name | What it is |
|------|-----------|
| `Agent(inner, *, tracer=None, cost=None, reliability=None, name=None)` | The facade. Wraps any callable / `BaseAgent`, adds the collaborators you pass, and returns a `RunResult`. `.run(input)` (sync) / `.arun(input)` (async). |
| `BaseAgent` | ABC defining the `arun()`/`run()` contract. Subclass it for a custom agent; `Agent` and `SupervisorAgent` are `BaseAgent`s. |
| `record_tool_call(name, args=None, result=None, *, success=True, latency=0.0, error=None)` | Call inside your agent to record a tool invocation onto the run (feeds tool metrics + traces). |
| `record_step(kind, content, **metadata)` | Record a reasoning/action step (feeds `PlanCoherence` + traces). |
| `Recorder` | The object those two functions write to; bound per-run by `Agent`. Rarely used directly. |

### Observability & cost

| Name | What it is |
|------|-----------|
| `Tracer(exporter="memory", service_name="agentargus")` | OpenTelemetry tracer. `exporter`: `"memory"` \| `"console"` \| `"otlp"`. Emits spans with GenAI conventions; supplies the run's `trace_id`. |
| `CostTracker(pricing=None, *, ceiling_usd=None, tracer=None)` | Prices reported token usage. `pricing`: `{model: (in_per_1M, out_per_1M)}`. `add_usage(usage, *, model, step)`, `total()`, `table()`; raises `CostCeilingExceeded` past the ceiling. |
| `Usage(input_tokens, output_tokens)` | A typed token-usage record you can pass to `add_usage`. |
| `get_logger(name=None)` | The one sanctioned logger factory (namespaced under `agentargus`). |
| `configure_logging(level="INFO", *, color=True, json_format=False, stream=None)` | Configure the root logger (color/JSON, TTY-aware). |

### Reliability

| Name | What it is |
|------|-----------|
| `ReliabilityPolicy(*, retry=None, retries=None, fallbacks=None, breaker=None, dead_letter=None, tracer=None)` | Composes strategies (**breaker → fallback → retry**) into one policy; pass to `Agent(reliability=...)`. |
| `RetryWithBackoff(max_attempts=3, *, base_delay=0.5, max_delay=30.0, jitter=0.1, retryable=(...))` | Exponential backoff + jitter; retries only transient errors by default. |
| `FallbackChain(alternatives)` | Ordered list of alternative callables/agents; tries the next on any failure. |
| `CircuitBreaker(failure_threshold=5, *, cooldown=30.0)` | Thread-safe CLOSED→OPEN→HALF_OPEN state machine; fails fast when open. |
| `DeadLetterQueue(sink)` / `JsonlDeadLetterSink(path)` | Persists permanently-failed inputs; JSONL sink is the default backend. |

### Evaluation

| Name | What it is |
|------|-----------|
| `Metric` | ABC for all metrics; `compute(run_result_or_dict) -> float`. |
| `EvalSuite(metrics)` | Runs a `list[Metric]`; `run(source) -> {name: score}`, `score(result) -> RunResult`. |
| `EvalDataset` | Load eval cases: `load(source)` (str path / list / dict) or `from_jsonl(path)`. |
| `EvalCase(question, reference=None, contexts=(), metadata={})` | One dataset case. |
| `EvalRunner(concurrency=8)` | Batches an agent over a dataset: `run(agent, dataset, suite) -> EvalReport`. |
| `EvalReport` | `summary()`, `regressions(baseline, threshold=0.05)`, `to_html()`, `to_dict()`. |
| **RAG metrics** (need a `judge=`) | `Faithfulness`, `AnswerRelevance` (optional `embedder=`), `ContextPrecision`, `ContextRecall` (needs a `reference`). |
| **Agent metrics** | `ToolUseAccuracy` (needs `expected_tools`), `ToolSuccessRate`, `ErrorRecoveryRate` (all no-LLM), `PlanCoherence` (needs a `judge=`). |

### Orchestration

| Name | What it is |
|------|-----------|
| `SupervisorAgent(workers, *, router, max_steps=10, checkpointer=None, run_id=None, tracer=None, dead_letter=None, name=...)` | Routes to `{name: BaseAgent}` workers, follows a handoff chain. Is-a `BaseAgent`. |
| `Handoff(target, input, context={})` | Returned by a worker to pass control to the next worker. |
| `LLMRouter(judge, descriptions=None)` | Default router — an LLM picks the best worker. (Any `route(input, workers)->name` works.) |
| `SqliteCheckpointer(path=":memory:")` | Durable (WAL) per-step checkpoint store; enables resume across a restart. |

### Human-in-the-loop

| Name | What it is |
|------|-----------|
| `Checkpoint(backend, *, name="checkpoint", checkpointer=None, run_id=None)` | A pause point: `await require_approval(context) -> Decision`; rejection raises `CheckpointRejected` (recorded as a controlled failure). |
| `Decision(approved, reason=None, edited_input=None)` | The approval result; `edited_input` lets a human redirect the run. |
| `ApprovalBackend` | Protocol: `async decide(context) -> Decision`. |
| `CallbackApprovalBackend(fn)` | Wrap any sync/async callable as a backend (Slack/UI/API). |
| `ConsoleApprovalBackend()` | Prompts stdin (fails safe to reject in non-TTY/CI). |
| `AutoApproveBackend()` / `AutoRejectBackend(reason=...)` | Always approve / reject — for tests and policy. |

### Seams & configuration

| Name | What it is |
|------|-----------|
| `Judge` | Protocol you inject for LLM-as-judge: `complete(prompt) -> str` (optional `complete_batch`). No client bundled. |
| `Embedder` | Optional protocol for embeddings: `embed(texts) -> list[list[float]]` (used by `AnswerRelevance`). |
| `AgentArgusConfig` | Cross-cutting config resolved from env + kwargs (`from_env(**overrides)`). |
| `batch_complete(judge, prompts)` | Helper that uses a judge's `complete_batch` if present, else loops. |

### Core objects

| Name | What it is |
|------|-----------|
| `RunResult` | The canonical, immutable result: `output`, `trace_id`, `spans`, `cost`, `tool_calls`, `steps`, `errors`, `scores`, `metadata`; `with_scores()`, `to_dict()`/`from_dict()`. |
| `Span`, `ToolCall`, `Step`, `ErrorRecord`, `CostBreakdown` | The value objects that populate a `RunResult`. |

### Exceptions

| Name | Raised when |
|------|-------------|
| `AgentArgusError` | Base class for all AgentArgus errors. |
| `ConfigError` | Invalid configuration (e.g. malformed cost ceiling) — fails fast. |
| `CostCeilingExceeded` | Accumulated spend crosses the configured ceiling. |
| `TransientError` | Marker you raise to signal a retryable failure. |
| `CircuitOpenError` | The circuit breaker is OPEN and refuses the call. |
| `OrchestrationError` | A supervisor problem (loop / unknown worker / oversized context). |
| `CheckpointRejected` | A HITL checkpoint was rejected (caught → recorded as a controlled failure). |
| `SerializationError` | A `RunResult` field can't be serialized (names the field). |

`agentargus.__version__` holds the installed version string.

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
