# AgentArgus — HARD_QUESTIONS

Questions a skeptical staff engineer / interviewer would ask about each module.
**Claude Code writes the questions; the owner writes the answers.** A module's
gate does not close until the owner can answer its batch in their own words.

---

## Module 2 — Observability: Tracer

1. You register two `SpanProcessor`s — the exporter's and your `CollectorProcessor`.
   Walk through what happens to a single span from `on_end` to landing in
   `RunResult.spans`. What's the per-span overhead, and why is it acceptable?

2. `CollectorProcessor` buffers spans in a dict keyed by trace id and only frees
   a buffer on `collect()`. Describe a usage pattern that leaks memory. What
   guarantees `Agent.arun` doesn't leak, and what would you add to protect the
   direct-`Tracer`-use case?

3. The OTel trace id is now the source of truth, replacing Module 1's uuid4.
   What exactly happens to `RunResult.trace_id` and to the log-correlation
   contextvar at the moment the span opens? Trace the ordering — is there a
   window where a log line carries the uuid4 instead of the OTel id?

4. `SimpleSpanProcessor` exports synchronously on the calling thread. For a
   high-throughput production agent, what's the consequence, and when would you
   switch to `BatchSpanProcessor`? What do you lose by batching?

5. You convert OTel nanosecond timestamps to float seconds. What precision do
   you lose, and could two spans ever get the same `start_time` after
   conversion? Does anything in the system depend on them being distinct?

6. The tracer depends on OTel's `SpanExporter` *interface*, not a concrete
   backend (the abstraction pillar). Name a second exporter you could drop in
   with zero changes to `Tracer`, and one you couldn't — why?

7. `current_trace_id()` reads `get_current_span()`. In an async run with multiple
   concurrent agent tasks, does each task see its own current span, or could
   they cross-contaminate? Justify via OTel's context propagation + contextvars.

8. Nested spans nest via OTel's context (child's `parent_id` = parent's
   `span_id`). But when an `Agent` wraps another `Agent`, each has its own
   `Tracer` with its own provider — so the inner spans are in a *different*
   trace. Is that right, or should they share one trace? Argue it.

9. `Tracer.__init__` builds a fresh `TracerProvider` per instance rather than
   using the global OTel provider. Why? What breaks if two `Tracer`s fought over
   the global provider, and what do you give up by not using it?

10. Your `[otlp]` extra is optional and `_make_exporter` raises a friendly
    ImportError if it's missing. Is deferring that import to call-time the right
    call versus importing at module top? What's the trade-off for startup and
    for discoverability of the dependency?

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
