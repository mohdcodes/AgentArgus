# AgentArgus — HARD_QUESTIONS

Questions a skeptical staff engineer / interviewer would ask about each module.
**Claude Code writes the questions; the owner writes the answers.** A module's
gate does not close until the owner can answer its batch in their own words.

---

## Module 3 — Observability: Cost Tracking

> Context-block format: each question has a Deep Research Agent example, a code
> citation, and Claude Code's own answer. Read it, then write yours below.

### 1. User-supplied pricing vs. a shipped price table

**Context:**
- *Research-agent example:* you run the research agent on `claude-opus-4-8`
  today at $15/$75 per 1M; next month the price changes. With a shipped table,
  your cost report would silently be wrong until we released an update.
- *Code:* `PriceTable` (`_internal/pricing.py`) starts empty; prices come from
  `CostTracker(pricing=...)` or `register_model`.
- *My answer:* we ship NO prices. The user supplies (model, input/1M, output/1M).
  Trade-off: slightly more setup for the user, but the number is always the one
  *they* know is current, and we never maintain a drifting table or guess. This
  is the same honesty principle as the fail-fast cost ceiling.

*Your answer:*

### 2. add_usage overload — why does Usage not fall into the object branch?

**Context:**
- *Research-agent example:* the retrieval step hands the tracker a typed
  `Usage(input_tokens=4000, output_tokens=150)`; the synthesis step hands it a
  raw Anthropic response object. Both must be priced correctly.
- *Code:* `CostTracker.add_usage` (`observability/cost.py`) has three
  `@overload`s: `dict`, `Usage`, `object` — registered in that order.
- *My answer:* first-match-wins + `Usage` is a distinct class registered *before*
  `object`, so `isinstance(u, Usage)` matches first and the `object` catch-all
  only catches provider responses. Verified by `test_overload_sites.py` — all
  three branches hit their own impl. If `object` were registered first, a
  `Usage` would wrongly take the catch-all.

*Your answer:*

### 3. The bare-`dict` annotation and the type: ignore

**Context:**
- *Research-agent example:* the planner passes `{"input_tokens": 1200,
  "output_tokens": 300}` — a plain dict — and expects it priced.
- *Code:* the dict overload is annotated `usage: dict` (not `dict[str, Any]`)
  with a `# type: ignore[type-arg]`.
- *My answer:* `methodoverload` dispatches via `isinstance(value, annotation)`,
  and `isinstance(x, dict[str, Any])` raises `TypeError` — subscripted generics
  aren't valid isinstance targets. So the annotation MUST be bare `dict` for
  dispatch to work; the `type: ignore` documents that this is deliberate, not
  laziness. Same family of constraint as the no-`__future__`-annotations rule.

*Your answer:*

### 4. Per-step ledger vs. a single aggregate

**Context:**
- *Research-agent example:* the run cost $0.21 total, but you need to see that
  retrieval alone was $0.071 (4000 input tokens) to know where to optimize.
- *Code:* `CostEntry` rows in `CostTracker._entries`; exposed via `entries`,
  `table()`, and `RunResult.metadata["cost_ledger"]`. `total()` sums them.
- *My answer:* the ledger is the source of truth; `total()` is derived by summing
  entries (reusing `CostBreakdown.__add__`). This gives per-step attribution
  (which step, how many in/out tokens, cost) AND the aggregate, without storing
  the total separately (no risk of them drifting out of sync).

*Your answer:*

### 5. Cost span emission — coupling cost to the tracer

**Context:**
- *Research-agent example:* you open the trace in Jaeger and want each LLM call
  (planner/retrieval/synthesis) to appear as its own span with token + cost
  attributes under the `agent.run` span.
- *Code:* `CostTracker._record` opens `self._tracer.span(SPAN_LLM_CALL, ...)`
  when a tracer was injected; otherwise it just records to the ledger.
- *My answer:* the tracer is optional on `CostTracker` — no tracer means ledger
  only, no spans. When shared with the Agent's tracer, each priced call nests a
  cost span under `agent.run`. Risk: if the caller gives the tracker a
  *different* tracer than the Agent, the cost spans land in a different trace —
  documented; pass the same `Tracer` to both.

*Your answer:*

### 6. Ceiling check timing — before or after recording?

**Context:**
- *Research-agent example:* the ceiling is $0.15; the synthesis step's usage
  pushes the total to $0.21. You want to know *which* entry blew the budget.
- *Code:* `_record` appends the `CostEntry`, THEN calls `_check_ceiling`, which
  raises `CostCeilingExceeded` if `total() > ceiling`.
- *My answer:* I record first, then check — so the offending entry is in the
  ledger when the exception fires and you can see exactly what tripped it. The
  run halts mid-flight with a partial-but-accurate ledger. Alternative (check
  first, reject the entry) would hide the tripping call. Trade-off is
  intentional.

*Your answer:*

### 7. Provider-reported tokens vs. local counting

**Context:**
- *Research-agent example:* the synthesis LLM call returns
  `response.usage.output_tokens = 800`. Should we trust that or re-count with a
  tokenizer?
- *Code:* `add_usage` reads the provider's counts (dict keys / `Usage` fields /
  `response.usage.*`); there is no tokenizer in the codebase.
- *My answer:* trust the provider's counts — they're what you're *billed* on, so
  they're the accurate basis for cost. A local tokenizer would be an estimate
  that can diverge from the invoice and would add a dependency. If a provider
  ever doesn't report usage, that's a future fallback, not a default.

*Your answer:*

### 8. Unknown-model behaviour — warn vs. raise

**Context:**
- *Research-agent example:* someone runs the agent on `claude-opus-5` (not yet
  priced) — should the whole run crash, or continue with token counts but $0?
- *Code:* `PriceTable.price` logs a WARNING and returns a `CostBreakdown` with
  tokens but zero cost when the model is unregistered.
- *My answer:* count tokens, $0 cost, loud WARNING. Crashing a long research run
  over a missing price is worse than continuing with visible-but-unpriced usage;
  the WARNING (naming the model) makes the gap impossible to miss. We never
  pretend $0 is the *real* cost — the warning says it's unpriced.

*Your answer:*

### 9. CostBreakdown is immutable — how does the running total accumulate?

**Context:**
- *Research-agent example:* three steps each produce a `CostBreakdown`; the run's
  total must be their sum.
- *Code:* `total()` folds `summed = summed + entry.cost` using
  `CostBreakdown.__add__` (Module 0), starting from an empty `CostBreakdown`.
- *My answer:* `CostBreakdown` is a frozen dataclass, so accumulation is
  functional — each `+` returns a new breakdown, never mutating. `total()`
  recomputes from the ledger each call rather than caching a mutable running
  sum, so there's no stale-total bug. Cost: O(n) per `total()` call, negligible
  for realistic entry counts.

*Your answer:*

### 10. The ledger lives on the tracker, copied to metadata — dual source of truth?

**Context:**
- *Research-agent example:* after the run you read `result.metadata["cost_ledger"]`
  — but the live `CostTracker` also still holds `.entries`. Which is canonical?
- *Code:* `Agent.arun` calls `self._cost.table()` and stores the rows in
  `RunResult.metadata`; the tracker keeps its own `_entries`.
- *My answer:* the tracker is canonical during the run; `RunResult.metadata` is
  an immutable *snapshot* taken at result-assembly time (plain dict rows, frozen
  into the RunResult's read-only mapping). They can't drift because the snapshot
  is taken once and the RunResult is immutable. A tracker reused across runs
  would accumulate — so use one tracker per run (documented).

*Your answer:*

---

## Module 2 — Observability: Tracer

> **Format note (permanent from this module on).** Every question carries a
> **Context** block: a Deep Research Agent example (planner → retrieval →
> synthesis → evaluator), a real code citation, and Claude Code's own
> implementer answer. Read the Context, then write YOUR answer below it and
> check it against mine.

### 1. Two span processors — the per-span path and its cost

**Context:**
- *Research-agent example:* the research `Agent` runs; its `agent.run` span
  ends. That one span must both (a) ship to Jaeger so you can open the trace in
  the UI, and (b) appear in `RunResult.spans` so the evaluator can later read
  `PlanCoherence` off the step tree.
- *Code:* `Tracer.__init__` (`observability/tracer.py`) adds two processors —
  `SimpleSpanProcessor(self._exporter)` and `self._collector`
  (`CollectorProcessor`). On span end, both `on_end`s fire.
- *My answer:* each span triggers one export (I/O) via the exporter's processor
  and one in-memory `list.append` via the collector. The overhead of the
  collector is a dict lookup + append + `move_to_end` — microseconds, no I/O —
  negligible beside the export. It's acceptable because it's the only way to get
  `RunResult.spans` populated *without* turning off export; the two consumers
  (Jaeger, RunResult) are served from one span end.

*Your answer:*

### 2. CollectorProcessor buffer — leak risk and the guard

**Context:**
- *Research-agent example:* someone instruments retrieval directly with
  `tracer.span("retrieval")` in a loop over 10k documents but never calls
  `collect()` — those span buffers would pile up under their trace ids.
- *Code:* `CollectorProcessor` (`observability/tracer.py`) — `_by_trace` is an
  `OrderedDict`; `_guard_growth` warns after `warn_after=3` uncollected traces
  and LRU-evicts (with an ERROR log naming the trace) at `max_traces=1000`.
  `Agent.arun` always calls `self._tracer.collect(trace_id)`, so normal use
  frees each buffer.
- *My answer:* the leak pattern is direct `Tracer.span` use with no `collect`.
  `Agent.arun` can't leak because it drains every run. For the direct-use case I
  added the warn-then-LRU-evict guard so growth is bounded and any drop is
  logged loudly — data loss is never silent (your review directive).

*Your answer:*

### 3. OTel trace id as source of truth — is there a uuid4 window?

**Context:**
- *Research-agent example:* the planner logs "decomposing query into 3
  sub-questions" at DEBUG. That log line must carry the SAME trace id that shows
  up in Jaeger, or you can't jump from log to trace.
- *Code:* `Agent.arun` (`agents/agent.py`) now opens the span FIRST, reads
  `otel_id = self._tracer.current_trace_id()`, calls `set_trace_id(trace_id)`,
  and only THEN emits the first `_logger.debug("agent.run start")`.
- *My answer:* **there WAS a window and I closed it in this review.** Originally
  the DEBUG "start" line was emitted under the uuid4 before the span opened —
  verified empirically (it logged `0c3a0a1a…` while the run's real id was
  `51c4da26…`). After the fix, every `agent.run` log line carries the OTel id;
  a test (`test_no_uuid4_window_with_tracer`) locks it in. uuid4 is used only
  when there's no tracer (`NullTracer.current_trace_id()` → None).

*Your answer:*

### 4. SimpleSpanProcessor is synchronous — production consequence

**Context:**
- *Research-agent example:* the synthesis step fans out 20 concurrent LLM calls,
  each a span. With `SimpleSpanProcessor`, each span end blocks on export I/O
  before returning.
- *Code:* `Tracer.__init__` uses `SimpleSpanProcessor(self._exporter)`.
- *My answer:* synchronous export adds latency on the hot path proportional to
  export round-trips — fine for tests/dev, bad under load. `BatchSpanProcessor`
  buffers and flushes on a background thread, removing that latency. What you
  lose: spans buffered-but-unflushed at a crash are gone unless you add a
  durability/checkpoint layer (your review point). Deferred to a production-
  hardening pass; documented in DESIGN_LOG §5.

*Your answer:*

### 5. Nanosecond → float seconds: precision and collisions

**Context:**
- *Research-agent example:* the planner emits two child spans ("parse intent",
  "plan steps") 200 ns apart. If both collapse to the same `start_time`, a
  timeline view can't tell which came first.
- *Code:* `Span` (`core/results.py`) now keeps `start_ns`/`end_ns` (lossless
  ints) alongside float `start_time`; `Span.sort_key` prefers ns.
  `CollectorProcessor.drain` sorts by `sort_key`.
- *My answer:* **collision is real and I verified it:** float64 holds ~16 sig
  digits, epoch-ns needs ~19, so two spans <~1µs apart get the *same* float
  (confirmed: 1 ns and even 100 ns apart both collided). Since `drain` orders by
  start time, that would scramble sibling order. Fix: keep integer nanoseconds
  internally (`sort_key`), convert to float only for display/API. A test asserts
  the float collides while `sort_key` does not.

*Your answer:*

### 6. Prove the SpanExporter abstraction is real

**Context:**
- *Research-agent example:* you want to switch the research agent's traces from
  local console output to a Zipkin backend without touching `Tracer`.
- *Code:* `_make_exporter` (`observability/tracer.py`) returns any
  `SpanExporter`; `Tracer` only depends on that interface.
- *My answer:* **compatible drop-in:** `ConsoleSpanExporter`, `InMemorySpanExporter`,
  a Zipkin exporter — all implement `SpanExporter.export(spans)` fire-and-forget,
  zero `Tracer` changes. **Incompatible:** anything demanding a *synchronous
  request/response per span* (a "confirm each span was stored before continuing"
  API) — our `SimpleSpanProcessor`/`BatchSpanProcessor` model is fire-and-export,
  not request/reply, so such a backend wouldn't fit without a different processor.

*Your answer:*

### 7. Concurrent runs — does current_trace_id cross-contaminate?

**Context:**
- *Research-agent example:* one process serves query A and query B at the same
  time (two `arun` tasks on one event loop). A's retrieval span must never be
  attributed to B's trace.
- *Code:* `Tracer.current_trace_id()` reads
  `opentelemetry.trace.get_current_span()`, which resolves through OTel context
  (contextvar-backed).
- *My answer:* no cross-contamination. OTel's context is contextvar-based, and
  `asyncio.create_task` snapshots contextvars per task (same mechanism as our
  own `trace_id` var, HARD_QUESTIONS Module 1 #10). So A and B each see their own
  current span. Two *unrelated* runs → two independent trace ids; within one
  run, parent/child link via `parent_id` under one trace id — two different
  mechanisms, not conflated (your review point).

*Your answer:*

### 8. Nested Agents → separate traces: right or wrong?

**Context:**
- *Research-agent example:* a supervisor research `Agent` delegates to a
  retrieval sub-`Agent`. Today the sub-agent's spans land in a *different* trace
  than the supervisor's.
- *Code:* each `Agent` is constructed with its own `Tracer` (→ own
  `TracerProvider`); `arun` collects only its own trace's spans.
- *My answer:* it's a defensible v0.1.0 choice: each Agent's run is an
  independent trace with its own id (`trace_id` + `span_id` + `parent_id` forms
  the tree *within* a run). A single unified cross-agent trace would need a
  shared provider / explicit context propagation — a deliberate future feature,
  not required now. The two ids are different-purpose, never merged (your review
  point). Trade-off documented.

*Your answer:*

### 9. Fresh TracerProvider per Tracer, not the global one

**Context:**
- *Research-agent example:* your test suite spins up many `Tracer()`s across
  parallel tests; none must leak spans into another's buffer, and the research
  demo's tracer must not clobber a host app's global OTel setup.
- *Code:* `Tracer.__init__` does `self._provider = TracerProvider()` (a fresh
  instance), never `trace.set_tracer_provider(...)`.
- *My answer:* isolation. A per-instance provider means two `Tracer`s (nested
  agents, parallel tests) never fight over shared global state. **Correction from
  an earlier wrong instinct:** multiple `Tracer()`s per process are explicitly
  ALLOWED and by design. Trade-off to document: we lose interop with OTel
  auto-instrumentation that discovers spans via the *global* provider — those
  tools won't see AgentArgus's spans. We do NOT restrict to one Tracer/process.

*Your answer:*

### 10. Deferred [otlp] import — right call?

**Context:**
- *Research-agent example:* a user runs the research demo with the default
  memory/console exporter and never installs `[otlp]`; importing `agentargus`
  must not fail for want of an OTLP package they don't use.
- *Code:* `_make_exporter` imports `OTLPSpanExporter` *inside* the `kind ==
  "otlp"` branch and raises an `ImportError` naming `pip install
  'agentargus[otlp]'` if it's absent.
- *My answer:* deferring to call-time is right — base install and `import
  agentargus` stay light, and only someone who actually selects `otlp` pays. The
  discoverability cost (failure at call-time, not import-time) is minimized by an
  excellent error message that names the exact install command (your review
  directive).

*Your answer:*

---

## Module 1 — Agents (`BaseAgent`, `Agent` facade)

1. You chose "async-core, sync-wraps": `run()` calls `asyncio.run(self.arun())`.
   What happens if a user calls `agent.run()` from inside a Jupyter notebook
   (which already runs an event loop)? Walk through exactly what your loop-guard
   does and why raising is better than the alternatives (nest_asyncio, a new
   thread, `run_until_complete`).

2. Sync inner callables are executed via `asyncio.to_thread`. That means they run
   in a worker thread and **cannot see** the `trace_id` contextvar. Is that a
   silent observability hole? When would it bite, and what's the fix you
   deliberately deferred?

3. `Agent.wrap` uses two `@overload`s — one on `BaseAgent`, one on `object`. Why
   `object` and not `typing.Callable`? Prove that ordering matters: what happens
   if the `object` overload is registered *before* the `BaseAgent` one?

4. `agent.py` is the only module without `from __future__ import annotations`.
   Explain precisely why the future import is incompatible with your own
   overload library. If a teammate "helpfully" adds it back, what breaks and how
   would you catch it in CI?

5. You wrapped the real collaborators as null objects. Isn't that just
   over-engineering for something that does nothing? Defend null-object over a
   simple `if self._tracer is not None` — in terms of what `Agent.arun` looks
   like across the next four modules.

6. `Agent` *is-a* `BaseAgent` (inheritance) but *has-a* tracer/cost/reliability
   (composition). Justify each choice. Why is inheriting from `BaseAgent`
   correct here but inheriting a tracer would be wrong?

7. `run()` blocks the calling thread until `arun` completes. If someone wraps a
   30-second agent and calls `run()` in a web request handler, what's the
   consequence? Is that AgentArgus's problem to solve?

8. The facade generates its own `trace_id` even when wrapping an inner
   `BaseAgent` that already produced one. Two trace_ids now exist for one
   logical run — is that a bug? How will this reconcile when the real Tracer
   (Module 2) creates spans with their own trace ids?

9. You put a `# type: ignore[no-redef]` on the second `wrap`. Isn't suppressing a
   type error a smell? What exactly can't mypy model here, and what would you
   lose by instead not using your overload library at this site at all?

10. `arun` sets the contextvar in a `try` and resets it in `finally`. If the
    inner call raises, the exception propagates but the reset still runs — trace
    it. Now: two `Agent.arun` calls running concurrently in the same event loop
    — do their trace_ids leak into each other? Why or why not (contextvars +
    tasks)?

---

## Module 0 — Core (`RunResult`, config, logging)

1. `RunResult` is `frozen=True` and stores collections as tuples — but a `dict`
   *value* nested inside `metadata` is still mutable. Is `RunResult` actually
   immutable? Where exactly does the immutability guarantee stop, and why did you
   choose to stop it there instead of deep-freezing recursively?

2. `with_scores` returns a new object instead of mutating. Prove that this
   actually prevents a class of bug — give a concrete two-evaluator scenario
   where in-place mutation of `scores` would corrupt state, and explain the
   memory cost of the copy-on-write approach when `spans` is large.

3. Trace correlation uses a `contextvars.ContextVar`. What happens to the
   trace_id when work is dispatched to a `ThreadPoolExecutor`? Does the child
   thread see it? What about an `asyncio.create_task`? Prove you know the
   difference and say which one AgentArgus relies on.

4. You chose raw ANSI codes over `colorama`. On an old `cmd.exe` without VT
   processing enabled, what does a colored log line look like? Is that a bug or
   acceptable, and what specifically prevents it from happening in practice?

5. `configure_logging` removes and re-adds handlers on every call. Is that
   thread-safe? What breaks if two modules call it concurrently at import time,
   and why is (or isn't) that a real risk?

6. The `Judge` protocol has a single synchronous `complete(prompt) -> str`
   method. RAG metrics may need to fire many judge calls per dataset row. Does a
   sync-only judge interface bottleneck batch eval? How would you add concurrency
   later without breaking the protocol's existing implementers?

7. `to_dict`/`from_dict` is a hand-written round-trip. `output` and tool
   `result` are typed `Any`. What happens when `output` is a non-JSON-serializable
   object (e.g. a numpy array or a custom class)? Where does that fail, and is
   failing there the right call?

8. Why is `RunResult` the single coupling point for the whole system? Defend it
   against the alternative where each module (tracer, cost, eval) defines its own
   result shape. What is the cost of this central aggregate when a future module
   needs a field none of the others care about?

9. `AgentArgusConfig.from_env` silently falls back to defaults on a malformed
   `AGENTARGUS_COST_CEILING_USD`. Is silent fallback the right behaviour for a
   *cost ceiling* — a safety limit? Argue both sides.

10. `slots=True` on the dataclasses — what did that buy you, and what did it cost
    you (think: pickling, multiple inheritance, adding attributes in
    `__post_init__` via `object.__setattr__`)? Why does `object.__setattr__`
    still work with slots + frozen?
