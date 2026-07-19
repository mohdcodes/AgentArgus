# Module 4 — Reliability: Design

> Per-module design doc, written **before** implementation and shaped by the
> start-of-module design answers. Approve this before code.

## Goal (spec §6.4, gate §5)
Replace the `PassthroughReliability` null-seam with real resilience:
`Agent(reliability=ReliabilityPolicy(...))` recovers from injected failures. Five
components on one abstraction — the **abstraction, encapsulation, and
polymorphism** OOP pillars all land here.

## Decisions locked (from the start-of-module questions)
1. **Recovery is auditable** — every failed attempt becomes an `ErrorRecord` on
   `RunResult.errors` with `attempt` # and `recovered` flag (True if a later
   attempt saved it, False on final give-up).
2. **Fallback = ordered list of callables/agents** — try next on any exception;
   framework-agnostic (a fallback is just another callable/`BaseAgent`).
3. **CircuitBreaker is thread-safe** — CLOSED/OPEN/HALF_OPEN counters guarded by
   a lock, correct under the async-core + `to_thread` reality.
4. **DLQ = JSONL sink behind a swappable `DeadLetterSink` interface.**
5. **Compose order: breaker → fallback → retry (inner); DLQ catches the final
   failure.** Retry a model a few times, then switch models, with the breaker
   gating the whole thing.
6. **Retry on a curated retryable set** (timeouts/connection/transient) by
   default, not programming errors; user-overridable.
7. **Recoveries/trips/fallbacks emit spans** (shared tracer) AND `ErrorRecord`s
   — visible in Jaeger and readable by eval metrics (Module 7).

## Files
```
agentargus/reliability/
├── __init__.py          # exports ReliabilityPolicy + strategies
├── base.py              # ReliabilityStrategy (ABC), RetryContext, ErrorRecord helpers
├── retry.py             # RetryWithBackoff
├── fallback.py          # FallbackChain
├── circuit_breaker.py   # CircuitBreaker (+ CircuitOpenError)
├── dead_letter.py       # DeadLetterSink (ABC) + JsonlDeadLetterSink + DeadLetterQueue
└── policy.py            # ReliabilityPolicy (composition + the seam __call__)
tests/unit/test_reliability.py
```

## The abstraction (pillar: abstraction + polymorphism)
```python
class ReliabilityStrategy(ABC):
    @abstractmethod
    async def execute(self, fn: Callable[[Any], Awaitable[Any]], ctx: RetryContext) -> Any: ...
```
Every strategy implements `execute`. `ReliabilityPolicy` composes them and
exposes the seam the Agent already calls: `async __call__(fn, inp) -> Any`. The
Agent's `arun` line — `await self._reliability(self._call_inner, input)` — does
not change.

`RetryContext` carries the shared run state a strategy needs: the tracer (for
spans), a sink to append `ErrorRecord`s to, the step label, and attempt bookkeeping.

## Component behaviour

### RetryWithBackoff (`retry.py`)
- Exponential backoff with jitter: `delay = base * 2**(attempt-1) * (1 ± jitter)`.
- Configurable `max_attempts`, `base_delay`, `max_delay`, `retryable` (tuple of
  exception types; default curated set: `TimeoutError`, `ConnectionError`, and a
  `TransientError` marker — NOT `ValueError`/`TypeError`).
- Non-retryable exception → re-raise immediately (don't waste attempts).
- Each failed attempt → span event + `ErrorRecord(attempt=n, recovered=?)`.
- **Sleep uses `asyncio.sleep`** on the async core (never blocks the loop).

### FallbackChain (`fallback.py`)
- Holds `[primary, alt1, alt2, ...]` (callables or `BaseAgent`s, normalised the
  same way `Agent.wrap` does — reuse that logic).
- On any exception from one, record an `ErrorRecord`, emit a fallback span, try
  the next. Exhausting the list re-raises the last exception.

### CircuitBreaker (`circuit_breaker.py`) — pillar: encapsulation
- Encapsulated state machine `CLOSED → OPEN → HALF_OPEN` behind
  `allow()` / `record_success()` / `record_failure()`. Internal counters are
  name-mangled/private.
- `failure_threshold` consecutive failures → OPEN; after `cooldown` → HALF_OPEN
  (one trial allowed); trial success → CLOSED, trial failure → OPEN.
- **Thread-safe:** a `threading.Lock` guards every state transition.
- When OPEN, `execute` fails fast with `CircuitOpenError` (records an
  `ErrorRecord`, emits a span) — does not call `fn`.

### DeadLetterQueue (`dead_letter.py`)
- `DeadLetterSink(ABC)` with `append(record: dict)`. `JsonlDeadLetterSink`
  writes append-only JSONL. `DeadLetterQueue` wraps a sink and is invoked by the
  policy when everything else has failed — persists the input + error for replay.

### ReliabilityPolicy (`policy.py`) — pillar: polymorphism / composition
- Constructed ergonomically: `ReliabilityPolicy(retries=3, fallbacks=[...],
  breaker=CircuitBreaker(...), dead_letter=JsonlDeadLetterSink("dlq.jsonl"))`.
- Builds the wrap order **breaker(fallback(retry(fn)))**; on the final
  unrecovered failure, hands the input to the DLQ and re-raises (so the Agent's
  own error handling still sees it).
- Iterates its strategies polymorphically — it knows only `ReliabilityStrategy`.

## Integration with Agent / observability / cost
- `Agent(inner, reliability=ReliabilityPolicy(...), tracer=..., cost=...)`. The
  policy receives the tracer via `RetryContext` so its spans nest under
  `agent.run`.
- `ErrorRecord`s produced by the policy are collected and attached to
  `RunResult.errors` — **this needs a small `Agent.arun` change**: capture the
  errors the policy accumulated and pass them into the `RunResult`. (The seam
  gains an errors channel; `PassthroughReliability` yields an empty one.)

## OOP pillars in this module
- **Abstraction:** `ReliabilityStrategy` ABC + `DeadLetterSink` ABC.
- **Encapsulation:** `CircuitBreaker`'s private, lock-guarded state machine.
- **Polymorphism:** `ReliabilityPolicy` composes/applies `list[ReliabilityStrategy]`
  without knowing concrete types.
- **Inheritance:** concrete strategies inherit `ReliabilityStrategy` (is-a).

## methodoverload
- **Not a site.** Reliability has no "same operation, different input types"
  dispatch. Waived in DESIGN_LOG (correct, not forced).

## Testing plan (spec §8 mandatory)
- Retry succeeds on the Nth attempt (injected transient failures); non-retryable
  re-raises immediately.
- Fallback moves to the next callable on failure; exhaustion re-raises last.
- Breaker opens after threshold, fails fast when OPEN, half-opens after cooldown,
  closes on trial success. A concurrency test hammering `record_failure` from
  threads proves the lock.
- DLQ captures a permanently-failed input (temp JSONL); sink is swappable.
- `ReliabilityPolicy` composed order behaves (retry-within-fallback-within-breaker).
- `Agent(reliability=...)` recovers from an injected failure and the recovery
  shows up in `RunResult.errors` (recovered=True) + as spans.

## Failure modes to document (for DESIGN_LOG)
- Retry sleeps consume wall-clock; a high `max_attempts` × `base_delay` can
  stall a run — bounded by `max_delay` and attempt cap.
- Breaker shared across unrelated runs trips globally — intended (protects a
  shared dependency) but must be understood.
- DLQ JSONL sink is append-only, not deduplicated; replay tooling is out of scope
  for v0.1.0.

## Gate
`Agent(reliability=ReliabilityPolicy(retries=3, ...))` recovers from an injected
failure; all mandatory tests green; ruff + mypy clean; ≥80% coverage; DESIGN_LOG
+ Context-block HARD_QUESTIONS + `module_notes/module4.md`.
