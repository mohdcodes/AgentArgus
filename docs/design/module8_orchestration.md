# Module 8 — Orchestration Patterns: Design

> Per-module design doc, written before implementation, shaped by the
> start-of-module answers. Approve before code.

## Production-grade hardening (owner: "this is an important module")
Beyond the correct baseline, this module is hardened for real deployment. Every
item reuses an existing AgentArgus seam — **no new dependencies**:

- **Durability (crash-safety):** SQLite in **WAL mode**, each step written in a
  single committed transaction, and a `status` column (`running`/`completed`/
  `failed`). On resume a `running`/half-written step is *re-run*, not trusted.
- **Concurrency:** writes serialized by a lock (SQLite is single-writer),
  **every query scoped by `run_id`** so runs never cross-contaminate, WAL for
  concurrent readers. Documented limit under very heavy parallel writes.
- **Failure handling:** a worker failure → step checkpointed `failed`, an
  `ErrorRecord` recorded, the run optionally **dead-lettered (reuse Module 4
  DLQ)**, and a **partial `RunResult`** returned (chain-so-far, `recovered=False`)
  — never a bare crash. Composes with a `ReliabilityPolicy` wrapping each worker.
- **Observability:** a **tracer span per routing hop** nested under `agent.run`
  (reuse Module 2) + **structured INFO logging of every routing decision**
  correlated by `trace_id` (reuse Module 0 logging) — the "why did it route
  there?" audit trail.
- **Validation (fail fast):** reject an empty worker set, duplicate worker names,
  and a router that returns an unknown worker — at construction/route time with
  clear errors, not mid-run.
- **Guards:** `max_steps` cap AND a **context-size cap** on accumulated handoff
  context to prevent unbounded memory growth on a long chain.

## Goal (spec §6.6, gate §5)
Multi-agent coordination: `SupervisorAgent` routes to `workers: list[BaseAgent]`,
a `Handoff` primitive transfers control + context between workers, and a SQLite
checkpointer persists per-step state so a run resumes across a process restart
(the gate). `SupervisorAgent` **is-a** `BaseAgent`, so the whole multi-agent
system composes with Modules 1–7 (wrap it in `Agent`, trace/cost/eval it).

## Decisions locked (start-of-module answers)
1. **Routing:** pluggable `Router` — LLM-judge default, injectable custom
   `router(input, workers) -> BaseAgent`. Framework-agnostic, testable with a
   fake router, no forced LLM.
2. **Flow:** route-to-one per step; a worker may hand off, forming a sequential
   chain (retrieval → calculation → synthesis). Final worker's output = result.
3. **Handoff signal:** a worker whose output **is** a `Handoff` continues the
   chain; any other output is the final result. No exceptions-as-control-flow.
4. **Loop safety:** configurable `max_steps` (default 10); each hop recorded as a
   `Step`; exceeding raises `OrchestrationError` (controlled, catchable by
   reliability).
5. **Handoff payload:** `Handoff(target, input, context)` — target worker name,
   its input, and an accumulated context dict (serializable for checkpointing).
6. **Checkpointer:** per-step `{run_id, step, worker, input, output, status}` to
   SQLite behind a `Checkpointer` interface; same `run_id` resumes from the last
   completed step.

## Files
```
agentargus/agents/patterns.py        # SupervisorAgent, Router (+ LLM default), Handoff
agentargus/agents/checkpoint_store.py # Checkpointer (ABC) + SqliteCheckpointer + InMemory
agentargus/_internal/exceptions.py    # + OrchestrationError
tests/unit/test_patterns.py
tests/unit/test_checkpoint_store.py
```

## Handoff (`patterns.py`)
```python
@dataclass(frozen=True)
class Handoff:
    target: str                       # worker name to route to next
    input: Any                        # input for the target
    context: Mapping[str, Any] = {}   # accumulated state (docs, reasoning, ...)
```
A worker returns a `Handoff` to continue; the supervisor looks up `target` by
name among its workers and runs it with `handoff.input`, threading `context`
forward (merged across hops).

## Router (`patterns.py`)
```python
class Router(Protocol):
    def route(self, input: Any, workers: dict[str, BaseAgent]) -> str: ...

class LLMRouter:   # default; needs a Judge
    # prompts the judge with worker names+descriptions, returns a worker name
```
- Default `LLMRouter(judge)` picks the best worker by name/description.
- Any callable/`Router` can be injected (rule-based, keyword, etc.).
- Tests use a `FakeRouter` returning a fixed name — deterministic.

## SupervisorAgent (`patterns.py`) — is-a BaseAgent, polymorphic over workers
```python
class SupervisorAgent(BaseAgent):
    def __init__(self, workers: dict[str, BaseAgent], *, router, max_steps=10,
                 checkpointer=None, run_id=None): ...
    async def arun(self, input) -> RunResult:
        # 1. route to first worker (router.route)
        # 2. loop: run worker; if output is a Handoff -> route to target, record
        #    a Step, checkpoint; else -> final output
        # 3. enforce max_steps -> OrchestrationError
        # 4. assemble RunResult (output, steps=chain, metadata=route trace)
```
- Iterates workers **polymorphically** — knows only `BaseAgent.arun` (the
  polymorphism pillar; this is *why* `BaseAgent` exists).
- Records each hop via the Module 7 recorder (`record_step`) so the chain shows
  up in `RunResult.steps` and `PlanCoherence` can score it.
- On resume (same `run_id` + checkpointer): skip steps already marked complete.

## Checkpointer (`checkpoint_store.py`)
```python
class Checkpointer(ABC):
    def save_step(self, run_id, step, worker, input, output, status): ...
    def load_steps(self, run_id) -> list[dict]: ...
    def last_completed_step(self, run_id) -> int: ...

class SqliteCheckpointer(Checkpointer):   # file or :memory:
class InMemoryCheckpointer(Checkpointer): # tests
```
- SQLite table `checkpoints(run_id, step, worker, input_json, output_json,
  status, ts)`. Inputs/outputs stored as JSON (via the Module 0 `_jsonable`
  approach; non-serializable → clear error).
- Resume: `SupervisorAgent` with a `run_id` that already has completed steps
  replays cached outputs for those steps and continues. **Survives a process
  restart** (the gate) because SQLite is on disk.

## Testing (spec §8)
- 3-worker supervisor routes to the correct worker (FakeRouter) and returns its
  output.
- Handoff: worker A returns `Handoff("B", ...)`; supervisor runs B next; context
  threads through; final output is B's.
- `max_steps` exceeded → `OrchestrationError`.
- Checkpointer: `save_step`/`load_steps` round-trip; `SqliteCheckpointer` on a
  temp file **persists across a fresh instance** (simulated restart); a run with
  an existing run_id resumes from the last completed step (doesn't re-run it).
- SupervisorAgent composes: `Agent(supervisor)` runs and produces a RunResult
  whose `steps` show the routing chain.

## OOP / reuse
- **Polymorphism:** SupervisorAgent over `list[BaseAgent]` (the headline pillar).
- **Inheritance:** SupervisorAgent is-a BaseAgent (so it nests in Agent, wraps,
  traces). **Abstraction:** `Checkpointer` ABC, `Router` protocol.
- Reuses `BaseAgent.arun`/`run` (Module 1), the recorder (Module 7),
  `_internal/exceptions`, Module 0 `_jsonable`.

## Failure modes (for DESIGN_LOG)
- `max_steps` too low aborts a legitimate long chain; too high delays loop
  detection. Default 10 with a clear error.
- LLM router can pick a nonexistent/at wrong worker name — validated against the
  worker set; invalid pick → OrchestrationError (not a silent misroute).
- SQLite JSON serialization fails on non-JSON inputs/outputs — surfaced as a
  clear error at checkpoint time, not silently dropped.
- Concurrent runs sharing a checkpointer file: keyed by run_id, but SQLite write
  contention under heavy parallelism is a documented limitation (WAL mode helps).

## Gate
A 3-agent supervisor runs, routes correctly, supports a handoff chain, and its
state persists to SQLite across a simulated process restart (resume from last
step). Deterministic tests (fake router/judge) green; ruff + mypy clean; ≥80%
coverage; DESIGN_LOG + Context-block HARD_QUESTIONS + module_notes/module8.md.
```
