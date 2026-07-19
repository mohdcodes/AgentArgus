# AgentArgus — DESIGN_LOG

A per-module decision record, written by Claude Code, so the owner learns the
system by reviewing decisions rather than typing every line. Newest entries at
the top.

---

## Module 9 — Human-in-the-Loop — 2026-07-19

### 1. What was built
- **`hitl/checkpoint.py`** — `Checkpoint.require_approval(context) -> Decision`,
  `Decision(approved, reason?, edited_input?)`, `ApprovalBackend` protocol +
  `CallbackApprovalBackend`, `ConsoleApprovalBackend`, `AutoApprove/AutoReject`.
- **`_internal/exceptions.py`** — `CheckpointRejected(checkpoint, reason)`.
- **`agents/agent.py`** — `arun` catches `CheckpointRejected` → records an
  `ErrorRecord(recovered=False)` + partial RunResult (`failed=True`), no crash.

### 2. Why this shape
- **Pluggable async-first backend.** Real HITL awaits a human over a network
  (Slack/UI/queue), so `decide` is async; a sync callback is auto-wrapped via
  `to_thread`. Console/Auto backends implement the same interface trivially.
- **Rejection = controlled failure** (spec §6.7). `raise CheckpointRejected`,
  caught by the run loop and recorded — a denied approval is auditable
  (error_type + reason in `RunResult.errors`), not an exception the caller must
  hand-handle. Reuses the exact partial-failure shape Module 8 established.
- **`edited_input`** lets a reviewer redirect the agent (approve-with-
  modification), not just yes/no — a common real HITL need.
- **Explicit placement.** The agent calls `await checkpoint(context)` at the
  risky point (like `record_tool_call`) — gate exactly what matters, nothing
  forced globally.
- **Pause/resume via Module 8 checkpointer.** A prior approval for
  `(run_id, name)` is replayed instead of re-prompting — survives a restart.
- **Console fail-safe:** non-TTY (CI) → reject with a log, never hang on stdin.

### 3. Reuse points introduced
- Reuses Module 8 `Checkpointer` (approval persistence/replay), Module 7 recorder
  (`record_step` — approvals show in `RunResult.steps`), Module 0 `ErrorRecord`
  + `AgentArgusError`. Almost pure composition.

### 4. methodoverload decision
- **Not a site.** Waived.

### 5. Failure modes
- Backend that never returns blocks the run — async lets the caller impose a
  timeout (built-in timeout wrapper deferred; documented).
- `edited_input` is trusted (no validation of the human's redirect) — documented.
- Resume replays an approval even if the world changed since — same
  "resume promptly" assumption as Module 8.

### 6. The one thing most likely to be asked in review
"A rejection raises — how is that not just a crash the user has to catch
everywhere?" Answer: the `Agent`/`Supervisor` run loop catches `CheckpointRejected`
and turns it into a first-class `ErrorRecord(recovered=False)` on a partial
`RunResult` with the reason — so a denial is a normal, inspectable outcome
(`result.metadata["failed"]`, `result.errors[0].reason`), never an unhandled
exception. The user gates the action; AgentArgus handles the denial.

---

## Module 8 — Orchestration Patterns (production-grade) — 2026-07-19

### 1. What was built
- **`agents/patterns.py`** — `SupervisorAgent(BaseAgent)` (routes to workers,
  follows a handoff chain), `Handoff(target, input, context)`, `Router` protocol
  + `LLMRouter` default.
- **`agents/checkpoint_store.py`** — `Checkpointer(ABC)`, `SqliteCheckpointer`
  (WAL, status flag, run_id-scoped, locked), `InMemoryCheckpointer`.
- **`_internal/exceptions.py`** — `OrchestrationError`.

### 2. Why this shape — production-grade hardening
The owner flagged this as an important module to make production-grade. Beyond a
correct baseline, every hardening item reuses an existing seam (no new deps):
- **Durability:** SQLite **WAL** + atomic per-step commit + a `status`
  (running/completed/failed) column — a step killed mid-write stays `running` and
  is re-run on resume, never trusted.
- **Concurrency:** writes serialised by a lock (SQLite is single-writer),
  **every query run_id-scoped** so concurrent runs never cross, WAL for readers,
  `check_same_thread=False` so `to_thread` workers can use the connection.
- **Graceful failure:** a worker exception → step checkpointed `failed`, an
  `ErrorRecord` recorded, optional DLQ (reuse Module 4), and a **partial
  RunResult** (`failed=True`, chain so far) returned — not a bare crash.
- **Observability:** per-hop tracer span (reuse Module 2) + structured
  routing-decision logs (reuse Module 0), correlated by run/trace id. Each hop
  also `record_step` (Module 7) so `PlanCoherence` can score the chain.
- **Validation (fail fast):** empty worker set, unknown router pick, unknown
  handoff target all raise `OrchestrationError` at construction/route time.
- **Guards:** `max_steps` (loop cap) **and** a ~1 MB context-size cap
  (runaway-accumulation guard).

### 3. Design decisions
- **Handoff as return value, not exception.** A worker whose output *is* a
  `Handoff` continues the chain; anything else is final. Avoids
  exceptions-as-control-flow (which would collide with the reliability layer that
  catches exceptions).
- **Polymorphism headline:** `SupervisorAgent` iterates `dict[str, BaseAgent]`
  knowing only `BaseAgent.arun` — this is *why* `BaseAgent` exists. And it
  *is-a* `BaseAgent`, so `Agent(supervisor)` wraps/traces/evals a whole
  multi-agent system.
- **Resume:** same `run_id` + checkpointer replays completed steps' cached
  outputs and continues — verified across a fresh `SqliteCheckpointer` instance
  (simulated restart) with zero worker re-runs.

### 4. methodoverload decision
- **Not a site.** Waived; no type-dispatch operation here.

### 5. Failure modes
- LLM router hallucinating a worker name — tolerant match against known names,
  else `OrchestrationError` (no silent misroute).
- SQLite write contention under very heavy parallel writes — WAL mitigates;
  documented limit.
- Non-JSON-serializable handoff input/output — `json.dumps(default=str)` coerces
  for storage; a truly unserializable object degrades to its `str()` in the
  checkpoint (documented; the live object still flows in-process).
- `max_steps`/context-cap defaults may be wrong for exotic workflows — both
  configurable, both raise clearly.

### 6. The one thing most likely to be asked in review
"Your checkpointer resumes across a restart — how do you know a step that was
mid-flight when the process died isn't wrongly trusted as complete?" Answer: a
step is written `running` *before* the worker runs and only flipped to
`completed` *after* it returns, in a committed transaction. On resume only
`completed` steps are replayed; a `running` (killed mid-flight) step is re-run.
The `status` column is exactly this crash-safety signal.

---

## Module 7 — Agent Metrics — 2026-07-19

### 1. What was built
- **`eval/metrics/agent.py`** — `ToolUseAccuracy`, `ToolSuccessRate`,
  `ErrorRecoveryRate` (all no-judge), `PlanCoherence` (LLM judge over steps).
- **`agents/recorder.py`** — `Recorder` + a contextvar + module-level
  `record_tool_call` / `record_step` the inner agent calls.
- **`agents/agent.py`** — `arun` binds a fresh `Recorder` per run and collects
  its `tool_calls`/`steps` onto the `RunResult`. Fills the standing gap: those
  fields were always empty tuples before.
- **`eval/metrics/base.py`** — `MetricInput` extended with tool_calls/steps/
  errors/expected_tools; extraction updated. Overload site #4 shape unchanged.

### 2. Why this shape — resolving "how do we judge correct tool use?"
The owner's real question. "Correct" is undefined without ground truth, so it
split into two honest metrics:
- **`ToolUseAccuracy`** = F1(expected names, actual names). **Requires** an
  author-supplied `metadata["expected_tools"]` label (like `reference` for
  ContextRecall); NOT_APPLICABLE without it — never guesses the "right" tool.
- **`ToolSuccessRate`** = successes/total from the `success` flag; works on any
  run with zero setup.
`ErrorRecoveryRate` reads Module 4's `recovered` flag (1.0 if no errors).
`PlanCoherence` judges steps (NOT_APPLICABLE if none). *Expected* comes from the
dataset; *actual* from the recorder — the same labels-in-dataset / behaviour-
from-run split as RAG.

### 3. Reuse points introduced
- `Recorder` contextvar mirrors `set_trace_id`/`reset_trace_id` exactly.
- Reuses `Metric`/`LLMJudgeMetric`/overload #4, `EvalSuite`, `RunResult` fields,
  Module 4's `recovered`. Three metrics are pure-heuristic — the concrete
  non-LLM `Metric` that proves the ABC isn't LLM-specific (checkpoint 6.1).

### 4. methodoverload decision
- **Not a new site.** `MetricInput` got richer but `compute` is still site #4
  (RunResult vs dict), unchanged.

### 5. Failure modes
- **ToolUseAccuracy is name-set F1** — ignores call order and arguments; an agent
  that calls the right tools in a nonsensical order still scores 1.0.
  Order/arg-sensitive matching is a documented future option.
- **Recorder + sync inner fn:** unlike trace_id, this DOES work under
  `asyncio.to_thread` — `to_thread` copies the context, and the `Recorder` is a
  *mutable object* we append to (not a rebind), so appends land on the same
  instance. Verified. A raw `threading.Thread` still needs the explicit
  `recorder=` escape hatch.
- **PlanCoherence** carries the same judge-subjectivity caveat as the RAG judges.

### 6. The one thing most likely to be asked in review
"Your ToolUseAccuracy is order- and argument-insensitive set F1 — an agent that
calls the right tools in the wrong order, or with wrong args, still scores 1.0.
Isn't that a hole?" Answer: yes, deliberately — v0.1.0 measures *tool selection*,
the most common failure. Order/args matching is a documented next step; the
metric's docstring and this log state the limitation so a reviewer isn't misled.

---

## Module 6 — Eval: Dataset + Runner + Report — 2026-07-19

### 1. What was built
- **`eval/dataset.py`** — `EvalCase` (question + optional reference/contexts/
  metadata) and `EvalDataset` with `load` = **methodoverload site #1**
  (str path / list records / dict single), `from_jsonl`, per-row validation.
- **`eval/runner.py`** — `EvalRunner` (async `gather` under a semaphore cap,
  sync `run` driver), `CaseResult` (run + scores + captured error).
- **`eval/report.py`** + **`templates/report.html.j2`** — `EvalReport`:
  `summary()`, `regressions(baseline, threshold)`, `to_html()`, `to_dict()`.

### 2. Why this shape
- **Concurrency via `asyncio.gather` + `Semaphore(8)`** — batches many cases
  fast without hammering the LLM API; the async-core from Module 1 pays off
  directly here. Cap is configurable.
- **Failing case is captured, not fatal** — `_eval_one` catches, records the
  error on the `CaseResult`, and the batch continues. One bad case in 50 doesn't
  lose the other 49's scores.
- **Case→metric bridge = scoring view.** The runner merges each case's
  question/reference/contexts into a copy of the `RunResult.metadata` (via
  `dataclasses.replace`) so metrics read them — and **agent-produced contexts
  win** over the case's pre-set ones. Agents aren't forced to know the eval
  metadata convention.
- **Regression = per-metric mean drop > threshold (0.05)** — interpretable,
  no scipy, catches real drops without flagging run-to-run LLM noise.
- **Self-contained HTML** (inline CSS, no external assets) so a report opens/
  attaches anywhere; `to_dict` covers programmatic/CI use.

### 3. Reuse points introduced
- Reuses `EvalSuite`/metrics (Module 5), `Agent.arun` (Module 1), `RunResult`,
  `ConfigError` (Module 3 family), and `jinja2` (a base dep since Module 0).
- The sync `run` loop-guard mirrors `BaseAgent.run` exactly — same pattern, no
  new invention.

### 4. methodoverload decision — site #1 (`EvalDataset.load`)
Used, cleanly type-dispatched on str/list/dict. Same recurring constraints: no
`from __future__ import annotations`; bare `str`/`list`/`dict` (scoped
`type: ignore[type-arg]`). Verified `load(42)` raises `NoMatchingOverloadError`.

### 5. Failure modes
- Concurrency cap too high → API rate limits; wrap the agent with Module 4's
  reliability to absorb. Default 8 is conservative.
- Mean-based regression is a blunt signal, not statistical proof — documented.
- The Jinja template is a non-`.py` asset; it MUST ship in the wheel or
  `to_html` fails at runtime. Added `[tool.hatch.build.targets.wheel].artifacts`
  and verified the template is inside the built wheel.

### 6. The one thing most likely to be asked in review
"Your regression check compares means with a fixed 0.05 threshold — how do you
avoid flagging normal LLM run-to-run variance as a regression, and how do you
avoid missing a real 0.04 drop?" Answer: the threshold trades sensitivity for
noise; it's a first-pass signal, configurable per call, and a statistical test
(bootstrap/t-test) is the documented upgrade path when datasets are large enough
to warrant it.

---

## Module 5 — Eval Metrics (RAG) — 2026-07-19

### 1. What was built
- **`eval/metrics/base.py`** — `Metric(ABC)` (compute = overload site #4),
  `MetricInput`, `LLMJudgeMetric` (holds injected judge, tolerant `_ask_json`),
  `NOT_APPLICABLE` sentinel, `cosine_similarity` helper.
- **`eval/metrics/rag.py`** — `Faithfulness`, `AnswerRelevance`,
  `ContextPrecision`, `ContextRecall`, methodology modeled on RAGAS.
- **`eval/suite.py`** — `EvalSuite` (polymorphic `run` → dict; `score` → new
  RunResult), NaN/NOT_APPLICABLE excluded.
- **`config.py`** — `Embedder` protocol (optional, for AnswerRelevance).

### 2. Why this shape — the RAGAS decision
The owner asked whether to use RAGAS. Decision: **model the methodology on RAGAS
(Apache-2.0), own the implementation, credit them — do NOT depend on `ragas`.**
Rationale: `ragas` pulls LangChain + a heavy tree, which contradicts the
single-package / minimal-dep bet in spec §1/§9, and RAGAS is named in §1 as the
*gap* AgentArgus fills. I verified the actual RAGAS formulas from their docs
(2026-07-19) before implementing, rather than guessing:
- Faithfulness = supported claims / total claims. (matched my draft)
- AnswerRelevance = mean cosine(gen_question_i, question) — **needs embeddings**;
  we added an optional `Embedder` protocol with a judge-scored fallback.
- ContextPrecision = **rank-aware Average Precision** (not a flat fraction) —
  corrected my draft after reading the real definition.
- ContextRecall = fraction of **ground-truth reference** claims attributable to
  context — needs a reference; returns NOT_APPLICABLE when absent (honest).

### 3. Reuse points introduced
- `LLMJudgeMetric._ask_json` — one home for judge-call + JSON-parse + tolerant
  fallback; all four metrics use it (no duplicated parsing).
- `MetricInput` + `_from_run_result`/`_from_dict` — one normalisation the
  overload feeds into; scoring logic isn't duplicated per input shape.
- `cosine_similarity` — pure-Python, no numpy dep.
- Reuses Module 0 `Judge`/`with_scores`; the `Embedder` mirrors the `Judge` seam.

### 4. methodoverload decision — site #4 (`Metric.compute`)
Used: `@overload` on `RunResult` vs `dict`, both → `MetricInput`. Same two
constraints as prior sites: no `from __future__ import annotations`; bare `dict`
(scoped `type: ignore[type-arg]`). Verified `compute(42)` raises
`NoMatchingOverloadError`.

### 5. Failure modes
- **Judge bias** can inflate scores — mitigated by decomposition (claims), not a
  single vague number; calibration remains a known open question (HARD_QUESTIONS).
- **Tolerant parsing** can mask a systematically-malformed judge; the WARNING is
  the signal and the conservative default avoids silently-high scores.
- **AnswerRelevance without an embedder** is an approximation (judge-scored), not
  RAGAS-exact — documented.
- **ContextRecall without a reference** is NOT_APPLICABLE, excluded from scores.

### 6. The one thing most likely to be asked in review
"Faithfulness uses an LLM judge — how do you stop the judge's bias from silently
inflating scores?" Answer: we force *decomposition* (claims + per-claim
supported flags) so the score is a ratio of checkable sub-judgments, not one
vague number; tests pin high/low with a fake judge; real calibration (judge vs.
human labels) is future work. And we credit RAGAS's methodology rather than
inventing our own unvalidated definition.

---

## Module 4 — Reliability — 2026-07-19

### 1. What was built
- **`reliability/base.py`** — `ReliabilityStrategy(ABC)` (`execute(fn, inp, ctx)`)
  and `RetryContext` (threads the tracer + error accumulator + step through the
  composed strategies).
- **`retry.py`** — `RetryWithBackoff`: exponential backoff + jitter, curated
  retryable set (`TransientError`/`TimeoutError`/`ConnectionError`), injectable
  sleeper for deterministic tests, `asyncio.sleep` by default.
- **`fallback.py`** — `FallbackChain`: ordered callables/agents, try-next on any
  exception, exhaustion re-raises last.
- **`circuit_breaker.py`** — `CircuitBreaker`: lock-guarded CLOSED/OPEN/HALF_OPEN
  state machine, fails fast with `CircuitOpenError` when OPEN.
- **`dead_letter.py`** — `DeadLetterSink(ABC)` + `JsonlDeadLetterSink` +
  `InMemoryDeadLetterSink` + `DeadLetterQueue`.
- **`policy.py`** — `ReliabilityPolicy`: composes breaker→fallback→retry, DLQ on
  terminal failure, exposes `last_errors`. It IS the `ReliabilitySeam`.
- **`agents/agent.py`** — `arun` attaches `self._reliability.last_errors` (duck-
  typed) to `RunResult.errors`.
- **`_internal/callables.py`** — extracted `to_async_callable`, shared by
  `Agent.wrap` and `FallbackChain` (one home for BaseAgent/sync/async normalisation).

### 2. Why this shape
- **breaker → fallback → retry (inner).** Retry a candidate a few times, then
  switch candidates, with the breaker gating the whole thing. Retry innermost
  avoids the retry×fallback attempt explosion the alternative ordering causes.
- **Every attempt is an `ErrorRecord`** with `attempt` and `recovered` — a
  recovered transient failure is still visible (early-warning signal), and eval's
  `ErrorRecoveryRate` (Module 7) reads exactly this.
- **Retryable set, not retry-everything.** Programming errors (ValueError/…)
  re-raise immediately — don't waste attempts/money on a deterministic bug.
- **Breaker is lock-guarded** — the honest answer to "is record_failure thread-
  safe?"; a concurrency test hammers it from 8 threads.
- **DLQ behind a `DeadLetterSink` ABC** — JSONL now, Redis/DB later, no queue
  change (abstraction pillar).

### 3. Reuse points introduced
- `to_async_callable` (`_internal/callables.py`) — the single BaseAgent/sync/
  async normaliser, now used by both `Agent.wrap` and `FallbackChain`.
- `RetryContext.record_error` / `.span` — one home for producing `ErrorRecord`s
  and reliability spans; every strategy uses it (no duplicated span/error code).
- Reuses Module 0 `ErrorRecord`, Module 2 tracer seam, Module 3 `CostCeilingExceeded`
  family in `_internal/exceptions.py`.

### 4. methodoverload decision
- **Not used — waived.** Reliability has no "same operation, different input
  types" dispatch site. Forcing it would be decorative.

### 5. Failure modes
- Retry sleeps consume wall-clock; bounded by `max_delay` and attempt cap. Tests
  inject a no-op sleeper so CI never waits.
- A breaker shared across unrelated runs trips globally — intended (it protects a
  shared dependency) but must be understood.
- On terminal failure the policy DLQs then re-raises, so `Agent.arun` currently
  propagates the exception (the recovered case is the gate). A future refinement
  could turn a terminal failure into a `RunResult` with `recovered=False` errors
  rather than raising.
- JSONL DLQ is append-only, not deduplicated; replay tooling is out of scope.

### 6. The one thing most likely to be asked in review
"Your circuit breaker shares state across threads — is `record_failure`
thread-safe? Prove it." Answer: yes — every transition is inside a
`threading.Lock`, counters are name-mangled/private, and a test spawns 8 threads
each calling `record_failure` 1000× and asserts the state stays consistent
(OPEN, no corruption). The lock is correct under the async-core + `to_thread`
reality where sync inner calls run on worker threads.

---

## Module 3 — Observability: Cost Tracking — 2026-07-19

### 1. What was built
- **`observability/cost.py`** — `CostTracker` (prices reported usage, keeps a
  per-step ledger, emits a cost sub-span per entry, enforces a ceiling), plus
  `Usage` (typed token record) and `CostEntry` (one ledger row).
- **`_internal/pricing.py`** — private `PriceTable`: `model → (input_per_1m,
  output_per_1m)`. **User-supplied**, dollars per 1M tokens. No baked-in prices.
- **`_internal/exceptions.py`** — `CostCeilingExceeded(total, ceiling)`.
- **`conventions.py`** — `SPAN_LLM_CALL`, `GEN_AI_USAGE_COST_USD`.
- **`agents/agent.py`** — `arun` attaches the per-step cost ledger to
  `RunResult.metadata["cost_ledger"]` (duck-typed; NullCostTracker unaffected).

### 2. Why this shape
- **User provides pricing (per 1M tokens), not a baked-in table.** Provider
  prices drift; a stale hardcoded number is worse than asking for the truth.
  This also erases the "unknown/outdated model price" maintenance burden and
  makes cost honest. Register via constructor dict or `register_model`.
- **Provider-reported token counts** (the billed-on figures), not a local
  tokenizer estimate — accurate and dependency-free.
- **Itemized per-step ledger** (owner request): `CostEntry` rows record *which
  step*, input/output tokens, and cost; surfaced via `entries`, `table()`, and
  on `RunResult.metadata`. Aggregate is `total()`.
- **Every priced call also emits a cost sub-span** (shared tracer) so spend is
  visible in the trace timeline, not only in the aggregate.
- **Ceiling raises `CostCeilingExceeded`** the instant a call crosses it — the
  fail-fast posture set for the ceiling config; Module 4 can catch it.
- **Unknown model → count tokens, $0 cost, loud WARNING** — never crash a run
  over a missing price, never silently claim a cost is known.

### 3. Reuse points introduced
- `PriceTable` — the single home for pricing math (`tokens/1e6 * rate`),
  private to `observability/` (encapsulation pillar).
- `CostBreakdown.__add__` (Module 0) reused to sum the ledger in `total()`.
- The cost span reuses Module 2's tracer seam + `conventions.py` keys.

### 4. methodoverload decision — site #2 (`add_usage`)
Used, genuinely type-dispatched on the *shape* the caller holds: `dict` /
`Usage` / `object` (provider response, catch-all, registered last). `cost.py`
omits `from __future__ import annotations` (the Module 1 finding). Note the
`dict` overload MUST be annotated as bare `dict`, not `dict[str, Any]` —
`isinstance(x, dict[str,Any])` raises, so a subscripted generic would break
dispatch; a scoped `# type: ignore[type-arg]` documents this. `Usage` is a
frozen dataclass (a distinct class), so it dispatches to its own overload before
falling through to `object`.

### 5. Failure modes
- `add_usage(object())` with no readable usage raises `TypeError` naming the
  type — deliberate, better than recording zero silently.
- The ceiling check runs *after* recording the entry, so the offending entry is
  in the ledger when the exception fires (you can see what tripped it). The run
  is halted mid-flight — a partial ledger, by design.
- Prices are floats; extreme token counts accumulate normal float error, well
  within the 5% gate. Verified against a hand-computed fixture.

### 6. The one thing most likely to be asked in review
"Costs are floats — how accurate is the total, and where does drift come from?"
Answer: each entry is `tokens/1e6 * rate` in float64; error is far below the 5%
gate and dominated by whether the *provider's* reported token counts match the
invoice, not our arithmetic. We price exactly what the provider reports.

---

## Module 2 — Observability: Tracer — 2026-07-19

### 1. What was built
- **`observability/tracer.py`** — `Tracer`, a thin OO wrapper over
  `opentelemetry-sdk`. Encapsulates provider/exporter/processor setup; exposes
  `span(name, **attrs)` (context manager), `current_trace_id()`,
  `collect(trace_id)`, and a `@traced` decorator (sync + async).
- **`CollectorProcessor`** — a private `SpanProcessor` that buffers finished
  spans keyed by trace id, so spans reach `RunResult.spans` *and* the real
  exporter simultaneously.
- **`conventions.py`** — extended with canonical span names (`SPAN_AGENT_RUN`,
  `SPAN_TOOL_CALL`) and operation-name values (`OP_INVOKE_AGENT`,
  `OP_EXECUTE_TOOL`).
- **`agents/agent.py`** — `arun` now adopts the OTel trace id as source of truth
  and drains spans onto the `RunResult`. `agents/seams.py` — `TracerSeam` gains
  `current_trace_id`/`collect`; `NullTracer` returns `None`/`()`.
- **`pyproject.toml`** — added the `[otlp]` optional extra
  (`opentelemetry-exporter-otlp-proto-http`).

### 2. Why this shape
- **Exporter chosen from a string, wiring hidden (encapsulation).** `memory`
  (tests), `console` (dev), `otlp` (Jaeger/collector, optional). `_make_exporter`
  is the single place that maps names to exporters.
- **Collector processor runs alongside the exporter, not instead of it.** This
  is what lets `RunResult.spans` be populated even while exporting to Jaeger —
  export and capture are not mutually exclusive.
- **OTel trace id is the source of truth** when a real tracer is active; the
  Module 1 uuid4 is now the fallback for the untraced (`NullTracer`) path. This
  resolves the "placeholder id" open item from Module 1 and matches the
  HARD_QUESTIONS #2 answer (one W3C trace id per run, no competing schemes).
- **`Agent.arun` structure barely changed** — it still calls
  `with self._tracer.span(...)`. Swapping `NullTracer` for `Tracer` is a
  constructor argument; the orchestration body only *reads* two new seam methods.
  This is the null-object seam bet from Module 1 paying off exactly as designed.

### 3. Reuse points introduced
- `conventions.py` is now consumed by both `Agent` (operation name) and the
  Tracer/tests (span names) — the single source of truth for OTel keys.
- `_readable_to_span` — the one place OTel `ReadableSpan` → our `Span` dataclass
  conversion lives (ns→s, parent linkage, attribute copy).

### 4. methodoverload decision
- **Not used in Module 2.** No "same operation, different runtime types" site
  here. Correctly waived.

### 5. Failure modes
- OTel `start_time`/`end_time` are integer nanoseconds; we divide by 1e9 to get
  float seconds. If a span is never ended (shouldn't happen with the context
  manager) `end_time` could be `None` → coerced to 0.
- `CollectorProcessor` buffers by trace id and only frees on `collect()`. A run
  whose trace is never drained would leak that buffer; `Agent.arun` always
  drains, but a user calling `Tracer.span` directly and never `collect`-ing
  would accumulate. Documented.
- `SimpleSpanProcessor` exports synchronously (fine for tests/dev). Production
  under load may want `BatchSpanProcessor`; deferred until it matters.

### 6. The one thing most likely to be asked in review
"You attach TWO span processors — the exporter's and your collector's. Isn't
that double work per span?" Answer: yes, one extra in-memory append per span,
which is negligible next to export I/O, and it's the price of getting
`RunResult.spans` without disabling export. The collector does no I/O.

### 7. Post-review fixes (from the Module 2 HARD_QUESTIONS pass)
The owner's review surfaced three issues; all fixed before closing the gate:
- **uuid4 trace-id window (Q3) — CLOSED.** The DEBUG "agent.run start" line was
  being emitted under the uuid4 fallback *before* the span opened (verified
  empirically). `arun` now opens the span, adopts the OTel id, and binds the
  contextvar *before* the first log line. Test: `test_no_uuid4_window_with_tracer`.
- **float-seconds collision (Q5) — FIXED.** Verified that two spans <~1µs apart
  collapse to the same float `start_time`. Added lossless `start_ns`/`end_ns`
  to `Span` and a `sort_key`; `CollectorProcessor.drain` orders by ns.
- **unbounded collector buffer (Q2) — GUARDED.** `CollectorProcessor` now warns
  after 3 uncollected traces and LRU-evicts the oldest at a 1000-trace cap, with
  an ERROR log naming exactly what was dropped (no silent data loss).

### 8. Process change (permanent, from this module forward)
HARD_QUESTIONS now carry a **Context block** per question: a Deep Research Agent
example, a real code citation, and Claude Code's own implementer answer for the
owner to check against. Recorded so every future module follows suit.

---

## Module 1 — Agents (`BaseAgent`, `Agent` facade) — 2026-07-19

### 1. What was built
- **`agents/base.py`** — `BaseAgent(ABC)`: abstract `arun(input) -> RunResult`
  (the real contract) and a concrete `run()` that drives `arun` via
  `asyncio.run`, refusing (with a clear error) to run inside an existing loop.
- **`agents/agent.py`** — `Agent(BaseAgent)`, the facade. Normalises any inner
  target into one async callable; orchestrates a run in its final shape; hosts
  the `wrap` overload (site #3).
- **`agents/seams.py`** — null-object seams (`NullTracer`, `NullCostTracker`,
  `PassthroughReliability`) plus their `Protocol` contracts (`TracerSeam`,
  `CostSeam`, `ReliabilitySeam`).
- **`observability/conventions.py`** — GenAI semantic-convention keys, single
  source of truth (needed one key now; Module 2 extends it).
- **`logging.py`** — added `reset_trace_id(token)` (proper contextvar restore).

### 2. Why this shape
- **Async-core, sync-wraps.** All orchestration lives in `arun`; `run` just
  drives it. This is the biggest reuse decision in the project — reliability,
  tracing, cost, HITL are written once on the async path and sync borrows them.
  Cost: `run()` inside a running loop is a controlled error, not a nested loop.
- **Composition, not inheritance, for collaborators.** `Agent` *has* a tracer /
  cost / reliability; it is not one. Injected as null objects now.
- **Null-object seams over `if x is not None`.** `Agent.arun` is written in its
  FINAL shape today; Modules 2/3/4/9 swap real objects in with zero edits to
  `Agent`. The null classes double as the documented contract each real
  collaborator must satisfy.
- **Sync callables run via `asyncio.to_thread`** so a blocking user function
  never stalls the event loop; async callables are awaited directly.

### 3. Reuse points introduced
- `reset_trace_id` in `logging.py` — the sanctioned contextvar restore; `Agent`
  is its first consumer.
- `observability/conventions.py` — every future span-attribute write imports its
  key from here (no scattered string literals).
- The seam `Protocol`s — the single contract later collaborators implement.

### 4. methodoverload decision — site #3 (`Agent.wrap`)
Used, and it genuinely dispatches. Two hard-won findings, both now in
`docs/concepts/methodoverload.md`:
- **`from __future__ import annotations` breaks dispatch.** PEP 563 stringizes
  annotations; the library does `isinstance(value, annotation)` at runtime, and
  `isinstance(x, "BaseAgent")` raises. `agent.py` therefore omits the future
  import. **This constrains every future overload site** (cost, dataset,
  metrics) to do the same.
- **A plain method overwrites an `@overload`.** The library only merges
  `@overload`-decorated siblings. So the callable "fallback" is itself an
  `@overload` dispatching on `object` (matches anything), registered *after* the
  `BaseAgent` overload — first-match-wins routes `BaseAgent` to its branch and
  everything else to the catch-all. This is the honest resolution of spec
  §4.3's callable caution: dispatch really happens, on `object` not a fictional
  `Callable`. mypy can't model this runtime pattern, so the second def carries a
  scoped `# type: ignore[no-redef]` with an explanatory comment.

### 5. Failure modes
- `run()` inside a running event loop raises rather than deadlocking — correct
  for a library, but a caller who doesn't read the message may be surprised.
- Sync callables run in a worker thread, so they do **not** see the `trace_id`
  contextvar (contextvars don't cross into raw threads without `copy_context`).
  Documented; async callables see it correctly. If trace correlation inside sync
  inner functions ever matters, we'll propagate the context explicitly.
- The generated `trace_id` (uuid4) is a placeholder until the real Tracer
  (Module 2) supplies the span's trace id; the seam is designed for a clean swap.

### 6. The one thing most likely to be asked in review
"You run sync callables in a thread — so your trace_id contextvar silently
doesn't reach them. Isn't that a correlation hole?" Answer: yes for sync inner
functions, by design (async is the primary path); the fix (`copy_context`) is
known and cheap, deferred until a real need appears.

---

## Module 0 — Core (`RunResult`, config, logging, scaffold) — 2026-07-19

### 1. What was built
- **`agentargus/core/results.py`** — the canonical `RunResult` plus its value
  objects `Span`, `ToolCall`, `Step`, `ErrorRecord`, `CostBreakdown`. All are
  frozen dataclasses. `RunResult` carries `output`, `trace_id`, `spans`, `cost`,
  `tool_calls`, `steps`, `errors`, `scores`, `metadata`, plus `with_scores`,
  `to_dict`, and `from_dict`.
- **`agentargus/config.py`** — `AgentArgusConfig` (env + kwargs, `from_env`) and
  the `Judge` protocol (the LLM-as-judge seam).
- **`agentargus/logging.py`** — `get_logger` factory, `ColorFormatter`,
  `JsonFormatter`, `configure_logging`, and `contextvars`-based trace
  correlation (`set_trace_id` / `get_trace_id`).
- **`agentargus/__init__.py`** — the (deliberately small) public API surface.
- Scaffolding: `pyproject.toml` (hatchling), `.ruff.toml` (with `T20` to ban
  `print()` in library code), `.gitignore`, CI matrix on 3.10/3.11/3.12,
  README/LICENSE.

### 2. Why this shape
- **Deep immutability via tuples, not just `frozen=True`.** Every collection
  field is coerced to a `tuple` in `__post_init__`, and every mapping to a
  `MappingProxyType`. `frozen=True` alone leaves `result.spans.append(...)`
  legal — a real hole. This was decided explicitly with the owner and directly
  answers the §11 HARD_QUESTION "is `spans` actually immutable?" with a provable
  *yes*. Cost: a caller who wants to build a result incrementally must assemble
  the collections first, then construct once. Acceptable — `RunResult` is an
  *output* aggregate, not a builder.
- **`with_scores` returns a new object.** Checkpoint 2.1 tempts a mutate-in-place
  `scores`. We reversed that in favour of immutability: eval consumers get a
  fresh `RunResult`, so a result handed to two evaluators can't be corrupted by
  one. `dataclasses.replace` keeps it a one-liner.
- **`Judge` as an injected protocol, no bundled client.** Base install stays
  dependency-light (spec §1/§9) and provider-agnostic. A concrete Anthropic
  adapter will live behind the `[dev]` extra for tests/demo. Second possible
  implementation (proving the abstraction earns its keep, checkpoint 6.1): a
  recorded/replay judge for deterministic CI, or a local-model judge.
- **`contextvars` for trace correlation, not a passed-around logger.** The
  trace_id is set once by `Agent.run()` and read by the log filter. It survives
  async boundaries and keeps trace_id out of every signature (checkpoint 7.1).
- **Raw ANSI over `colorama`.** Modern Windows terminals handle ANSI natively
  and we only colorize on a TTY, so the dependency isn't justified. Color is
  gated by three independent conditions: config flag AND not `NO_COLOR` AND
  `stream.isatty()`.

### 3. Reuse points introduced
- **`_freeze_mapping`** — the single home for turning a dict into a read-only
  mapping; used by every value object. No duplicated freezing logic.
- **`get_logger`** — the *only* sanctioned logger entry point. `logging.getLogger`
  is never called elsewhere in library code (enforced socially + by review).
- **`_TraceIdFilter`** — one place that injects trace_id onto records; both
  formatters just read `record.trace_id`.
- **`AgentArgusConfig`** — the single home for env access; no module reads
  `os.environ` directly.

### 4. methodoverload decision
- **Not used in Module 0**, correctly. Module 0's inputs are not type-dispatched
  — there is no "same operation, different runtime types" site here. Forcing it
  would be decorative. Its designated sites are Modules 3, 5, 6 (and cautiously
  1).
- **Studied the library end-to-end before relying on it** (owner's explicit ask:
  use it *gracefully*, not forcefully). Read the installed source and PyPI
  metadata; wrote a verified reference at `docs/concepts/methodoverload.md`.
  Findings that change how we'll use it:
  - **Public API is exactly three names**: `overload`, `OverloadedFunction`,
    `NoMatchingOverloadError`. The spec's §4 instruction to import `OverloadMeta`
    is wrong for v0.1.7 — `OverloadMeta` exists only as an *internal, unexported*
    class in `metaclass.py`. We will **not** reach into it; `@overload` alone
    handles methods via frame inspection + the `__get__` descriptor, verified.
  - **Dispatch is `isinstance`**, subclasses match their base, **no generics**,
    **first-match-wins**, and there is a real **subtype ordering trap** (`bool`
    is a subclass of `int`: register the most specific type first). All verified
    empirically. Documented so every use site orders its overloads correctly.
  - **Callables have no distinct `isinstance` class** — confirmed. So
    `Agent.wrap` (site #3) overloads on `BaseAgent` only and routes plain
    callables through a non-overloaded fallback, exactly per spec §4.3. We record
    *why* instead of pretending it dispatched.

### 4a. Post-review improvements (from HARD_QUESTIONS answers)
The owner's answers to the Module 0 HARD_QUESTIONS proposed four behaviours
better than the first cut. All were implemented before closing the gate:
- **#5** — `configure_logging` now takes a `threading.Lock` and installs the new
  handler via an atomic list swap (build-then-swap), so it can never be observed
  half-configured even under concurrent misuse.
- **#6** — batched judging is exposed via a `batch_complete(judge, prompts)`
  helper that probes for an optional `complete_batch`; the `Judge` protocol keeps
  `complete` as its *only* required member so minimal adapters still satisfy
  `isinstance` (adding `complete_batch` to the protocol would have broken that —
  caught by a failing test and reverted).
- **#7** — `to_dict` now runs `output` and each tool `result` through a
  `_jsonable` coercion that tries JSON, then `.to_dict()`, then raises a
  `SerializationError` naming the exact field. No silent lossy fallback.
- **#9** — a malformed `AGENTARGUS_COST_CEILING_USD` now raises `ConfigError`
  (fail-fast) instead of silently defaulting — correct for a safety limit.
Introduced `agentargus/_internal/exceptions.py` (`AgentArgusError`,
`ConfigError`, `SerializationError`) as the one home for library error types.

### 5. Failure modes
- If a caller stores a **mutable object inside** an otherwise-frozen field (e.g.
  a list as a `metadata` value), the top-level mapping is read-only but the
  nested list is still mutable. We freeze one level deep, not recursively. This
  is a deliberate, documented boundary (it's a HARD_QUESTION).
- `configure_logging` replaces handlers each call — fine for tests, but two
  threads calling it concurrently could race on handler list mutation. Not a
  concern in practice (configured once at startup) but noted.
- `from_dict` trusts its input shape; malformed dicts raise `KeyError`/`TypeError`
  rather than a friendly error. Acceptable for an internal round-trip helper.

### 6. The one thing most likely to be asked in review
"You froze the top level and the collections, but a `dict` *value* inside
`metadata` is still mutable. So is `RunResult` immutable or not?" — Answer: it is
immutable at the structural level (you cannot swap or grow any field); it is not
*recursively* deep-frozen for arbitrary nested user data, by design, because
recursively freezing unknown user payloads is expensive and surprising.
