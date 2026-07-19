# AgentArgus — Claude Code End-to-End Build Specification

**Author / Owner:** Mohd Arbaaz Siddiqui (`arbaazcode@gmail.com`)
**Repo:** `github.com/mohdcodes/agentargus`
**PyPI target:** `agentargus`
**Python:** 3.10+ (develop on 3.11)
**License:** MIT

> **What this file is.** This is the authoritative build spec for Claude Code to implement AgentArgus end-to-end. It is not a tutorial and not the old 12-week tactical plan. It defines *architecture, class contracts, the OOP rationale, logging design, the methodoverload integration, testing requirements, and the open-discussion checkpoints* that make the owner able to defend every design decision.
>
> **Non-negotiable working agreement (read before writing any code):**
> 1. **Build in the module order in §5.** Do not jump ahead. Each module must be green (tests + lint + types) before the next begins.
> 2. **Every module ends with a `DESIGN_LOG.md` entry** (see §11) written by you, Claude Code, explaining *what you built and why, what you rejected, and what would break it.* This is how the owner learns the system without hand-typing it.
> 3. **Every module ends with a `HARD_QUESTIONS.md` batch** (see §11) — 6–10 questions a skeptical senior engineer or interviewer would ask about that module. Do NOT answer them. The owner answers them.
> 4. **Reuse ruthlessly.** No copy-pasted logic across modules. If two modules need the same behaviour, it lives in one place (`_internal/` or a shared base class) and both import it. Call this out explicitly in the DESIGN_LOG whenever you extract something.
> 5. **Do not invent library APIs.** For `methodoverload` (the owner's own library) use only the verified API in §4. For any third-party API you are unsure about, stop and flag it rather than guessing.
> 6. **No secrets in code.** All keys via environment / `.env` (gitignored). Never hardcode tokens.

---

## Table of Contents

1. [Problem, Scope, and Non-Goals](#problem)
2. [Architectural Overview & Data Flow](#architecture)
3. [OOP Design Pillars — Where Each One Lives](#oop)
4. [methodoverload Integration Contract](#methodoverload)
5. [Module Build Order (dependency-ordered)](#build-order)
6. [Detailed Module Specifications](#module-specs)
7. [The Logging System (color, structured, correlated)](#logging)
8. [Testing Requirements (internal test cases per module)](#testing)
9. [Repository Layout](#repo)
10. [CI/CD, Packaging, Release](#cicd)
11. [DESIGN_LOG & HARD_QUESTIONS Protocol](#protocol)
12. [Definition of Done (per module + whole project)](#dod)

---

<a name="problem"></a>
## 1. Problem, Scope, and Non-Goals

### The problem
Teams ship LLM agents to production with almost no operational rigour. There is no single place to answer: *Did the agent do the right thing? What did it cost? Why did it fail? Can it recover?* RAGAS covers RAG evaluation only. Observability tools cover traces only. Reliability is hand-rolled per project. Nobody has a **framework-agnostic, single-package** answer.

### What AgentArgus is
A production-grade Python library that wraps *any* agent (a callable, a LangGraph graph, an arbitrary function) and gives it, uniformly:

- **Evaluation** — RAG metrics (faithfulness, answer relevance, context precision/recall) AND agent metrics (tool-use accuracy, plan coherence, error-recovery rate), scored against datasets with regression detection.
- **Observability** — OpenTelemetry traces following GenAI semantic conventions, plus accurate token/cost accounting.
- **Reliability** — retry with backoff, model fallback chains, circuit breaker, dead-letter queue.
- **Human-in-the-loop** — checkpoints that can pause a run for approval.
- **Orchestration helpers** — supervisor/worker and agent-to-agent handoff patterns.

### Non-goals (write these in DESIGN.md and defend them)
- Not a model-serving/inference engine (that's vLLM/TGI's job).
- Not a vector DB or a RAG framework itself — it *evaluates* RAG, it doesn't *do* retrieval for you (the demo app may, but the library doesn't mandate it).
- Not tied to any one agent framework. LangGraph is *supported*, never *required*.
- Not a hosted product in v0.1.0 — it's a library. A dashboard/app is a *consumer* of it (see the separate Jira-agent project).

**Open-discussion checkpoint 1.1:** Be ready to explain why "framework-agnostic wrapping" is the core design bet, and what the cost of that generality is (you lose framework-specific optimizations; you must define your own trace schema instead of piggybacking one).

---

<a name="architecture"></a>
## 2. Architectural Overview & Data Flow

### The central abstraction
Everything hangs off one wrap: `Agent(inner, ...)`. `inner` is whatever the user already has. AgentArgus decorates its execution with observability, reliability, and (optionally) HITL, and produces a rich `RunResult` that eval consumes.

```
                         ┌──────────────────────────────────────────┐
   user request  ─────▶  │                Agent (facade)             │
                         │  wraps: callable | LangGraph | BaseAgent  │
                         └───────┬───────────────────────┬───────────┘
                                 │                        │
                   reliability   │                        │  observability
                   (retry /      ▼                        ▼  (tracer + cost)
                    fallback /  ┌───────────────┐   ┌────────────────┐
                    breaker /   │ ReliabilityᴾLCY│   │  Tracer (OTel) │
                    DLQ)        └──────┬────────┘   │  CostTracker   │
                                       │            └───────┬────────┘
                                       ▼                    │
                              ┌────────────────┐            │
                     HITL ◀── │  inner.run()   │            │  emits spans + cost
                  checkpoint  └───────┬────────┘            │  onto RunResult
                                      │                     │
                                      ▼                     ▼
                             ┌──────────────────────────────────────┐
                             │              RunResult                │
                             │ output, trace_id, spans, cost, steps, │
                             │ tool_calls, errors, scores(after eval)│
                             └───────────────────┬──────────────────┘
                                                 │
                                                 ▼
                             ┌──────────────────────────────────────┐
                             │   EvalRunner  ──uses──▶  EvalSuite    │
                             │   (batch over EvalDataset)            │
                             │   EvalSuite = [Metric, Metric, ...]   │
                             └───────────────────┬──────────────────┘
                                                 ▼
                                    EvalReport (summary, regressions, to_html)
```

### The one canonical data object: `RunResult`
This is the spine of the whole system. Define it **first** (Week/Module 1), get it right, and everything else consumes it. Fields:

- `output: Any` — the agent's final answer
- `trace_id: str` — correlation id linking to spans
- `spans: list[Span]` — structured execution steps (from tracer)
- `cost: CostBreakdown` — tokens + dollars per model call (from CostTracker)
- `tool_calls: list[ToolCall]` — name, args, result, success/failure, latency
- `steps: list[Step]` — ordered reasoning/action steps (for plan-coherence eval)
- `errors: list[ErrorRecord]` — anything caught by reliability layer
- `scores: dict[str, float]` — populated *after* eval runs (empty at run time)
- `metadata: dict` — freeform

**Open-discussion checkpoint 2.1:** Be ready to explain why `RunResult` is the coupling point and why that's acceptable coupling (it's the domain's natural aggregate; the alternative — each module defining its own result shape — creates N×N adapters). Also be ready to defend `scores` being mutated post-hoc vs. returning a new object (immutability trade-off — see §3).

---

<a name="oop"></a>
## 3. OOP Design Pillars — Where Each One Lives

The owner explicitly wants all four OOP pillars used *meaningfully* (not decoratively). For each, this is where it MUST appear and why. Claude Code: implement exactly here, and justify in DESIGN_LOG.

### Abstraction
- `Metric` (ABC) — `compute(run_result) -> float`. Callers depend on the interface, never a concrete metric.
- `BaseAgent` (ABC) — defines the `run()` contract; concrete agents/wrappers implement it.
- `ReliabilityStrategy` (ABC) — retry, fallback, circuit-breaker, DLQ all implement a common `execute(callable, context)` interface.
- `SpanExporter` seam — depend on OTel's exporter interface, not a concrete backend.

### Encapsulation
- `CostTracker` hides the pricing tables and token-accounting math behind `add_usage(...)` / `total()`. Pricing tables live in `_internal/pricing.py` and are **private** — never import them outside the observability package.
- `CircuitBreaker` hides its state machine (`CLOSED → OPEN → HALF_OPEN`) behind `allow()` / `record_success()` / `record_failure()`. Internal counters are name-mangled/private.
- `OverloadCache` behaviour (from methodoverload) is used but never exposed.

### Inheritance
- Concrete metrics inherit `Metric`: `Faithfulness`, `AnswerRelevance`, `ContextPrecision`, `ContextRecall`, `ToolUseAccuracy`, `PlanCoherence`, `ErrorRecoveryRate`.
- Concrete reliability strategies inherit `ReliabilityStrategy`.
- **Rule:** inheritance is for *is-a* only. If you're tempted to inherit for code reuse without an is-a relationship, use composition instead and note it in DESIGN_LOG. (E.g. `Agent` does NOT inherit from `Tracer`; it *has* a tracer.)

### Polymorphism
- `EvalSuite` iterates `list[Metric]` and calls `metric.compute(run_result)` without knowing the concrete type — classic runtime polymorphism.
- `ReliabilityPolicy` composes multiple `ReliabilityStrategy` objects and applies them uniformly.
- **methodoverload-based polymorphism** (see §4): where a single conceptual operation legitimately takes different input *types*, use `@overload` for type-dispatched implementations instead of `isinstance` ladders.

**Open-discussion checkpoint 3.1:** Be ready to explain the difference between the inheritance-based polymorphism (EvalSuite over Metric) and the overload-based polymorphism (methodoverload), and *why each is used where it is*. A common interview trap: "why not just isinstance-branch?" Answer must cover open/closed principle and testability.

---

<a name="methodoverload"></a>
## 4. methodoverload Integration Contract

`methodoverload` is the owner's own PyPI library (`pip install methodoverload`, v0.1.7). **Verified API — use only these:**

```python
from methodoverload import overload, OverloadMeta, NoMatchingOverloadError
```

- `@overload` — decorates multiple same-named implementations; dispatch is by argument **type** at runtime via `isinstance()`.
- `OverloadMeta` — metaclass required for overloading **instance/class/static methods** inside a class:
  `class Foo(metaclass=OverloadMeta): ...`
- Works with `@classmethod` and `@staticmethod` (place `@overload` **outermost**, then `@classmethod`).
- `NoMatchingOverloadError` — raised when no signature matches; catch it explicitly where you offer a fallback.

**Verified limitations (design around these — do not fight them):**
- Dispatch uses `isinstance()`. **No generic types** — `List[int]` won't dispatch; only `list` will.
- Only positional/keyword args, no compile-time checking.
- First matching overload wins.

### Where methodoverload MUST be used (genuine fits, distinct runtime types)
1. **`EvalDataset` loading** — `load(source)` overloaded on `source: str` (path), `source: list` (in-memory records), `source: dict` (single record). Distinct types, clean dispatch.
2. **`CostTracker.add_usage(...)`** — overload on a raw usage `dict` vs. a typed `Usage` object vs. a provider-specific response object. Different callers pass different shapes; overload removes the isinstance ladder.
3. **`Agent` construction / wrapping target** — `wrap(inner)` overloaded on `inner: BaseAgent`, `inner: <callable>`... **CAUTION:** callables are not a distinct `isinstance` class in a clean way; verify behaviour with a test before relying on it. If it doesn't dispatch cleanly, fall back to a single method with an internal type check and record WHY in DESIGN_LOG. **Do not force methodoverload where it doesn't fit.**
4. **Metric input adaptation** — a metric's `compute` accepting either a full `RunResult` or a lightweight `dict` trace (useful for unit tests). Overload on `RunResult` vs `dict`.

### Where NOT to use it
- Anywhere dispatch would depend on generic parameterization (`list[str]` vs `list[int]`) — it can't tell them apart.
- Hot inner loops where the caching still adds overhead vs. a direct call — measure if unsure.

**Open-discussion checkpoint 4.1:** Be ready to explain, for each usage site, *why overload beats an isinstance ladder there* — and to honestly name the one or two places you tried it and backed off. "I used my own library everywhere" is a weaker answer than "I used it where type-dispatch was genuinely cleaner and here's where I decided it wasn't."

---

<a name="build-order"></a>
## 5. Module Build Order (dependency-ordered)

Build strictly in this order. Each is a hard gate.

| # | Module | Depends on | Gate before moving on |
|---|--------|-----------|----------------------|
| 0 | Repo scaffold + `RunResult` + logging + config | — | CI green, `RunResult` frozen, logger prints color |
| 1 | `agents/` — `BaseAgent`, `Agent` facade | 0 | wrap a trivial callable, `run()` returns `RunResult` |
| 2 | `observability/tracer.py` | 1 | every run emits OTel spans visible in Jaeger |
| 3 | `observability/cost.py` + `_internal/pricing.py` | 2 | cost within 5% of a hand-computed fixture |
| 4 | `reliability/` (retry, fallback, breaker, DLQ) + `ReliabilityPolicy` | 1 | `Agent(reliability=...)` recovers from injected failures |
| 5 | `eval/metrics/base.py` + RAG metrics | 1 | `EvalSuite([...]).run(result)` returns scores |
| 6 | `eval/dataset.py` + `eval/runner.py` + `EvalReport` | 5 | batch eval over JSONL → HTML report + regressions |
| 7 | `eval` agent metrics (tool-use, plan-coherence, recovery) | 6, 2 | unified suite handles RAG + agent metrics |
| 8 | `agents/patterns.py` (supervisor, handoff) + checkpointer | 1 | 3-agent supervisor runs, state persists (SQLite) |
| 9 | `hitl/checkpoint.py` | 4, 8 | a run can pause and resume on approval |
| 10 | `examples/deep_research_agent/` | all | end-to-end demo runs, produces eval report |
| 11 | docs + benchmarks + release | all | v0.1.0 on TestPyPI then PyPI |

**Rationale to defend (checkpoint 5.1):** why `RunResult` and logging come *before* any feature (everything depends on them), why observability precedes reliability (you can't verify recovery without traces), and why eval agent-metrics come *after* tracing (they read the trace).

---

<a name="module-specs"></a>
## 6. Detailed Module Specifications

For each module: the classes, their responsibilities, the key methods with signatures, and the reuse points. Claude Code implements these contracts; the owner reviews before you proceed.

### 6.0 Core (`agentargus/__init__.py`, `core/`, `config.py`, `logging.py`)
- `RunResult` — frozen dataclass per §2. Provide a `with_scores(scores)` method that returns a **new** RunResult (immutability; do not mutate in place). This is deliberately the opposite of the "mutate scores" temptation in checkpoint 2.1 — resolve it in favour of immutability and explain the reversal.
- Supporting dataclasses: `Span`, `CostBreakdown`, `ToolCall`, `Step`, `ErrorRecord`.
- `AgentArgusConfig` — central config object, populated from env + explicit kwargs. Encapsulates: default judge model, cost ceiling, tracer exporter choice, log level/color toggle.
- `logging.py` — see §7.

### 6.1 `agents/`
- `BaseAgent(ABC)` — `run(self, input: Any) -> RunResult` (abstract). `arun` async variant.
- `Agent(BaseAgent)` — the **facade**. Composition, not inheritance, for its collaborators:
  - `self._inner` (wrapped target)
  - `self._tracer` (Tracer | None)
  - `self._cost` (CostTracker | None)
  - `self._reliability` (ReliabilityPolicy | None)
  - `self._hitl` (Checkpoint | None)
  - `run()` orchestration order: open trace span → apply reliability wrapper around `inner` call → (HITL pause point) → collect cost → assemble `RunResult`.
- `wrap(inner)` — methodoverload usage site #3 (with the documented caution).

### 6.2 `observability/tracer.py` + `conventions.py`
- `Tracer` — thin OO wrapper over `opentelemetry-sdk`. Encapsulates exporter setup. Method `span(name, **attrs)` context manager; auto-instrument decorator `@traced`.
- `conventions.py` — constants for GenAI semantic-convention attribute keys (`gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, etc.). One source of truth; every span-attribute write imports from here (reuse point — no string literals scattered).

### 6.3 `observability/cost.py` + `_internal/pricing.py`
- `CostTracker` — `add_usage(...)` (overload site #2), `total() -> CostBreakdown`, cost-ceiling guard raising `CostCeilingExceeded`.
- `_internal/pricing.py` — private per-provider/per-model price tables `{model: (input_per_1k, output_per_1k)}`. Encapsulated; never imported outside `observability/`.

### 6.4 `reliability/`
- `ReliabilityStrategy(ABC)` — `execute(self, fn, ctx)`.
- `RetryWithBackoff(ReliabilityStrategy)` — exponential backoff + jitter; configurable max attempts.
- `FallbackChain(ReliabilityStrategy)` — ordered list of alternative callables/models; on failure try next; catch `NoMatchingOverloadError` too if relevant.
- `CircuitBreaker(ReliabilityStrategy)` — encapsulated `CLOSED/OPEN/HALF_OPEN` state machine.
- `DeadLetterQueue` — persists permanently-failed inputs (start with JSONL sink; interface allows swapping).
- `ReliabilityPolicy` — **composes** strategies and applies them in order. Polymorphism site. `Agent(reliability=ReliabilityPolicy(retries=3, fallback_models=[...]))`.

### 6.5 `eval/`
- `Metric(ABC)` — `compute(self, run_result) -> float`; `name: str`. Overload site #4 (accept `RunResult` or `dict`).
- RAG metrics (LLM-judge based; judge model from config): `Faithfulness` (claim decomposition), `AnswerRelevance`, `ContextPrecision`, `ContextRecall`.
- Agent metrics (read the trace/steps): `ToolUseAccuracy` (expected vs actual tool calls), `PlanCoherence` (LLM judge over `steps`), `ErrorRecoveryRate` (from `errors` + recovery outcomes).
- `EvalSuite` — holds `list[Metric]`; `run(run_result) -> dict[str,float]`; polymorphic iteration.
- `EvalDataset` — `load(source)` overload site #1; `from_jsonl`, validation.
- `EvalRunner` — `run(agent, dataset, suite) -> EvalReport`, batch, async-capable.
- `EvalReport` — `summary()`, `regressions(baseline)`, `to_html()` (Jinja template).

### 6.6 `agents/patterns.py`
- `SupervisorAgent(BaseAgent)` — routes to `workers: list[BaseAgent]`; polymorphic over workers.
- Handoff primitive — structured transfer of control + context between agents.
- SQLite checkpointer for persistence.

### 6.7 `hitl/checkpoint.py`
- `Checkpoint` — a pause point; `require_approval(context) -> Decision`. Pluggable approval backend (console/callback in v0.1.0). Integrates with reliability (a rejected checkpoint is a controlled failure, not a crash).

**Open-discussion checkpoint 6.1:** For every ABC, be ready to name a concrete second implementation that *could* exist — proving the abstraction earns its keep (e.g. a non-LLM heuristic metric, a Redis-backed DLQ, a Slack approval backend). If you can't name one, the abstraction may be premature.

---

<a name="logging"></a>
## 7. The Logging System (color, structured, correlated)

The owner specifically wants a strong logging system with color and good detail. Requirements:

- **One logger factory** `get_logger(name)` in `agentargus/logging.py`. Never call `logging.getLogger` directly elsewhere (reuse point).
- **Colorized console output** for local dev: level-based colors (DEBUG grey, INFO green, WARNING yellow, ERROR red, CRITICAL bold red). Use ANSI codes directly (no heavy dep) OR `colorama` for Windows safety — pick one and justify. Color must be **auto-disabled** when output is not a TTY (piped/CI) and via a config flag. Respect `NO_COLOR` env convention.
- **Structured logs** — support a JSON formatter for production (machine-parseable) selectable via config; human+color formatter for dev.
- **Correlation** — every log line inside an agent run carries the `trace_id` (use a `contextvars.ContextVar` set by `Agent.run()` / the tracer, read by the formatter). This is the bridge between logs and traces — call it out in DESIGN_LOG as a deliberate observability design choice.
- **No print()** anywhere in library code. Ever. (Examples/CLI may print.)
- **Sensible levels:** span open/close = DEBUG, cost totals = INFO, retries/circuit trips = WARNING, dead-letter = ERROR.

**Open-discussion checkpoint 7.1:** Be ready to explain how logs and traces are correlated (the `contextvars` trace_id), and why you didn't just pass the logger around explicitly (contextvars survive across async boundaries and don't pollute signatures).

---

<a name="testing"></a>
## 8. Testing Requirements (internal test cases per module)

**The rule (from the owner's plan, kept):** every module gets its tests written *in the same PR as the implementation*. Untested code does not pass the module gate.

### Test pyramid
- **Unit (most):** one class/function; mock all LLM/network calls; <1s each.
- **Integration (some):** module interactions; recorded LLM responses via `vcr.py`/`pytest-recording`.
- **End-to-end (few):** the demo app; run in a dedicated CI job, not every push.

### Determinism strategies for LLM code
1. Mock the judge/LLM with a fixture returning canned JSON.
2. Snapshot/record real responses once, replay after.
3. Statistical asserts for e2e ("score>0.7 on ≥90% of dataset"), never exact equality.

### Mandatory internal test cases (minimum — Claude Code adds more)
- **RunResult:** immutability (`with_scores` returns new object, original unchanged); serialization round-trip.
- **Agent:** wraps callable; `run()` populates trace_id, cost, tool_calls; async path works.
- **Tracer:** spans emitted with correct GenAI convention keys; in-memory exporter assertions; nested spans nest.
- **CostTracker:** cost math correct to fixture; `add_usage` overload dispatches on all input types (**explicit test that methodoverload picks the right impl**, incl. a `NoMatchingOverloadError` case); cost ceiling raises.
- **Reliability:** retry succeeds on Nth attempt (injected transient failure); fallback moves to next model on failure; circuit breaker opens after threshold and half-opens after cooldown; DLQ captures permanent failure.
- **Metrics:** each metric high/low score with mocked judge; metric accepts both `RunResult` and `dict` (overload test).
- **EvalRunner:** batch aggregates; `regressions(baseline)` flags a deliberately worsened score; `to_html` produces valid HTML.
- **Patterns:** supervisor routes to correct worker; handoff preserves context; checkpointer persists+restores across process restart (temp SQLite).
- **HITL:** approval resumes; rejection produces controlled failure recorded in `errors`.
- **methodoverload sites (cross-cutting):** a dedicated `tests/unit/test_overload_sites.py` asserting each overloaded method dispatches correctly per type AND raises `NoMatchingOverloadError` on an unsupported type.

### Coverage
- Target **80% line coverage**; do not chase 100%. Report in CI (`--cov=agentargus --cov-report=xml`).

**Open-discussion checkpoint 8.1:** Be ready to explain how you test non-deterministic LLM behaviour without flaky CI, and why exact-match asserts on LLM output are a smell.

---

<a name="repo"></a>
## 9. Repository Layout

```
agentargus/
├── .github/workflows/{ci.yml, publish.yml}
├── agentargus/
│   ├── __init__.py                 # public API surface + __version__
│   ├── config.py                   # AgentArgusConfig
│   ├── logging.py                  # get_logger, formatters, color, contextvars
│   ├── core/
│   │   ├── __init__.py
│   │   └── results.py              # RunResult, Span, CostBreakdown, ToolCall, Step, ErrorRecord
│   ├── agents/
│   │   ├── base.py                 # BaseAgent (ABC)
│   │   ├── agent.py                # Agent facade (wrap = overload site)
│   │   ├── patterns.py             # SupervisorAgent, handoff
│   │   └── orchestrator.py
│   ├── eval/
│   │   ├── suite.py                # EvalSuite
│   │   ├── dataset.py              # EvalDataset (load = overload site)
│   │   ├── runner.py               # EvalRunner
│   │   ├── report.py               # EvalReport (+ jinja html template)
│   │   └── metrics/{base.py, rag.py, agent.py}
│   ├── observability/
│   │   ├── tracer.py
│   │   ├── conventions.py          # GenAI semantic-convention keys (single source)
│   │   └── cost.py                 # CostTracker (add_usage = overload site)
│   ├── reliability/
│   │   ├── base.py                 # ReliabilityStrategy (ABC)
│   │   ├── retry.py
│   │   ├── fallback.py
│   │   ├── circuit_breaker.py
│   │   ├── dead_letter.py
│   │   └── policy.py               # ReliabilityPolicy (composition)
│   ├── hitl/checkpoint.py
│   └── _internal/
│       ├── pricing.py              # PRIVATE price tables
│       └── exceptions.py           # NoMatchingOverloadError re-exported? NO — import from methodoverload
├── tests/{unit/, integration/, fixtures/golden_dataset.jsonl, conftest.py}
├── examples/deep_research_agent/{README.md, agent.py, run.py}
├── docs/{getting_started.md, architecture.md, concepts/}
├── benchmarks/{README.md, deep_research_baseline.py}
├── DESIGN.md                       # problem/scope/non-goals/decisions (owner-authored w/ your input)
├── DESIGN_LOG.md                   # per-module decision log (see §11)
├── HARD_QUESTIONS.md               # per-module interview questions (see §11)
├── pyproject.toml, .ruff.toml, .gitignore
├── README.md, LICENSE, CHANGELOG.md, CONTRIBUTING.md
```

### `pyproject.toml` key points
- Build backend: `hatchling`.
- Runtime deps: `opentelemetry-sdk`, `opentelemetry-api`, `jinja2`, `methodoverload>=0.1.7`. Keep the runtime dep list **minimal**; put judge-LLM clients behind optional extras.
- Optional extras: `dev` (pytest, pytest-cov, pytest-asyncio, ruff, mypy, build, twine, pytest-recording), `langgraph` (langgraph support), `docs` (pdoc).
- Author = Mohd Arbaaz Siddiqui; keywords include `llm, agents, evaluation, observability`.

---

<a name="cicd"></a>
## 10. CI/CD, Packaging, Release

- **`ci.yml`:** matrix Python 3.10/3.11/3.12 → install `.[dev]` → `ruff check .` → `ruff format --check .` → `mypy agentargus` → `pytest --cov=agentargus`. e2e demo runs in a separate, non-blocking job.
- **`publish.yml`:** on tag `v*` → build → `twine upload` using `PYPI_API_TOKEN` secret. Prefer Trusted Publishing (OIDC) if set up.
- **Release flow:** bump `__version__` in `__init__.py` + `pyproject.toml` → build → upload TestPyPI → install from TestPyPI in a clean venv and smoke test → tag → real PyPI → GitHub release from CHANGELOG.
- **The `agentargus` PyPI name should already be reserved** with a v0.0.0 stub per the original plan. If not yet reserved, do that first (it's cheap insurance).

---

<a name="protocol"></a>
## 11. DESIGN_LOG & HARD_QUESTIONS Protocol (how the owner learns without hand-typing)

This is the mechanism that replaces "typing every line" with "understanding every decision." **Mandatory after each module.**

### `DESIGN_LOG.md` — you (Claude Code) write this
Per module, append a dated entry with:
1. **What was built** — the classes/functions and their responsibilities, in plain English.
2. **Why this shape** — the key design decisions and the alternatives rejected (e.g. "composition over inheritance for Agent's tracer because…").
3. **Reuse points introduced** — what got extracted so nothing is duplicated, and who consumes it.
4. **methodoverload decision** — used here / not used here, and the honest reason.
5. **Failure modes** — what would break this module and how it degrades.
6. **The one thing most likely to be asked in review.**

### `HARD_QUESTIONS.md` — you write the questions, owner writes the answers
Per module, 6–10 questions a skeptical staff engineer would ask. Examples of the *level* expected:
- "Your circuit breaker shares state across threads — is `record_failure` thread-safe? Prove it."
- "Faithfulness uses an LLM judge — how do you stop the judge's bias from silently inflating scores? What's your calibration story?"
- "`RunResult` is frozen but `spans` is a list — is it actually immutable? What stops a caller mutating the list in place?"
- "Cost is 'within 5% of invoice' — where does the other 5% go, and when would it be worse?"

**Do NOT answer these.** Leave them for the owner. This is the explain-back test in written form.

### The owner's loop per module
1. Read your DESIGN_LOG entry.
2. Answer the HARD_QUESTIONS in their own words.
3. Anything they can't answer → they open a discussion (with you or with me) until they can.
4. Only then does the module gate close.

---

<a name="dod"></a>
## 12. Definition of Done

### Per module
- [ ] Code implements the §6 contract for that module.
- [ ] OOP pillar(s) assigned to it in §3 are present and justified.
- [ ] methodoverload used at its designated site OR explicitly waived in DESIGN_LOG with reason.
- [ ] All mandatory internal test cases (§8) pass; coverage ≥80% for the module.
- [ ] `ruff check`, `ruff format --check`, `mypy` all clean.
- [ ] No `print()`; logging used with correct levels + trace correlation.
- [ ] DESIGN_LOG entry written (by you).
- [ ] HARD_QUESTIONS batch written (by you), answered (by owner).
- [ ] CI green on 3.10/3.11/3.12.

### Whole project (v0.1.0)
- [ ] Modules 0–9 done; demo app (10) runs end-to-end and emits a real eval report.
- [ ] Demo app produces: traces in Jaeger, an accurate cost breakdown, an HTML eval report with RAG + agent metrics, and at least one deliberately-injected failure that reliability recovers from.
- [ ] `docs/architecture.md` has a diagram matching §2.
- [ ] `docs/getting_started.md` gets a new user to a first traced+evaluated run in <5 minutes.
- [ ] Benchmarks: naive baseline vs AgentArgus-instrumented (overhead quantified — be honest about the cost of instrumentation).
- [ ] Published to TestPyPI, smoke-tested in clean venv, then to real PyPI as v0.1.0.
- [ ] README badges (PyPI version, Python versions, CI, license, coverage).

---

## Appendix A — Instruction to Claude Code on AI-assistance discipline

The owner is deliberately using you (Claude Code) to build fast. That is fine and expected. To keep this a *portfolio-grade, defensible* project rather than an opaque generated blob, honour these:

- **Narrate decisions, not just code.** When you make a non-obvious choice, say why in the DESIGN_LOG, not just in a code comment.
- **Prefer the owner's own primitives.** `methodoverload` is theirs — use it where it genuinely fits (§4) and say where it doesn't.
- **Flag, don't fabricate.** If you're unsure of a third-party API (OTel semantic-convention keys, LangGraph checkpointer API, a provider's usage-response shape), stop and say "verify this" rather than inventing a plausible-looking call.
- **Keep the public API small.** `from agentargus import Agent, EvalSuite, EvalRunner, ReliabilityPolicy` and the metrics should be most of it. Everything else is internal.
- **One behaviour, one home.** Any time you'd duplicate logic, extract it and note the extraction.

## Appendix B — First actions for Claude Code (do these in order)

1. Confirm/scaffold repo structure per §9. Set up `pyproject.toml`, `.ruff.toml`, CI.
2. Implement **Module 0** completely: `RunResult` + supporting dataclasses, `AgentArgusConfig`, `logging.py` (color + contextvars + JSON formatter). Full tests. Gate.
3. Write the Module 0 DESIGN_LOG entry and HARD_QUESTIONS batch. **Stop and hand back to the owner** before Module 1.
4. Proceed module by module through §5, gating at each step, never batching multiple modules without owner sign-off.