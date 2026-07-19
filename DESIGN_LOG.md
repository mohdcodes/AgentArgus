# AgentArgus — DESIGN_LOG

A per-module decision record, written by Claude Code, so the owner learns the
system by reviewing decisions rather than typing every line. Newest entries at
the top.

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
