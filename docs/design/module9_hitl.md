# Module 9 — Human-in-the-Loop: Design

> Per-module design doc, written before implementation, shaped by the
> start-of-module answers. Approve before code.

## Goal (spec §6.7, gate §5)
Pause a run for human approval at a checkpoint and resume on the decision. A
`Checkpoint` gates a risky action; a pluggable `ApprovalBackend` obtains the
`Decision`; a rejection is a **controlled failure** (recorded in
`RunResult.errors`), not a crash. Pending approvals persist via Module 8's
checkpointer so a run resumes across a restart.

## Decisions locked (start-of-module answers)
1. **Pluggable `ApprovalBackend`:** async-first `decide(context) -> Decision`.
   Default = a user-supplied callback (any UI/Slack/API); `ConsoleApprovalBackend`
   for local dev; `AutoApprove`/`AutoReject` for tests. A sync callback is
   auto-wrapped via `to_thread`.
2. **`Decision(approved, reason?, edited_input?)`:** approve/reject + audit
   reason + optional human-edited input (approve-with-modification).
3. **Rejection = controlled failure:** raises `CheckpointRejected`
   (`AgentArgusError`); caught by Agent/Supervisor and recorded as an
   `ErrorRecord(recovered=False)` with the reason.
4. **Pause/resume via Module 8 checkpointer:** a pending approval writes a
   `pending_approval` step; an approved decision (even post-restart) resumes.
5. **Explicit invocation:** the agent calls `await checkpoint(context)` exactly
   where it matters (like `record_tool_call`); also usable as a Supervisor
   pre-worker hook.

## Files
```
agentargus/hitl/__init__.py
agentargus/hitl/checkpoint.py     # Checkpoint, Decision, ApprovalBackend (+ impls)
agentargus/_internal/exceptions.py # + CheckpointRejected
tests/unit/test_hitl.py
```

## Decision + backends (`checkpoint.py`)
```python
@dataclass(frozen=True)
class Decision:
    approved: bool
    reason: str | None = None
    edited_input: Any = None          # human tweak to what runs next

@runtime_checkable
class ApprovalBackend(Protocol):
    async def decide(self, context: Mapping[str, Any]) -> Decision: ...

class CallbackApprovalBackend:        # wraps a sync-or-async callable
class ConsoleApprovalBackend:         # prompts stdin (dev/local)
class AutoApproveBackend / AutoRejectBackend:  # tests / policy
```
- `CallbackApprovalBackend(fn)`: if `fn` is a coroutine fn, await it; else run in
  `to_thread` (async-first with sync adapter).

## Checkpoint (`checkpoint.py`)
```python
class Checkpoint:
    def __init__(self, backend: ApprovalBackend, *, name="checkpoint",
                 checkpointer=None, run_id=None): ...
    async def require_approval(self, context) -> Decision:
        # 1. (optional) persist a pending_approval step via the checkpointer
        # 2. decision = await backend.decide(context)
        # 3. record a Step (kind="approval") via the recorder
        # 4. if not approved -> raise CheckpointRejected(reason)
        # 5. persist decision (completed) ; return decision
    async def __call__(self, context) -> Decision:   # sugar for require_approval
```
- On a **rejected** decision: raises `CheckpointRejected` carrying the reason;
  the surrounding `Agent.arun`/`SupervisorAgent` records it as an `ErrorRecord`
  (this needs a tiny catch in the run loop — see integration).
- On **resume**: if the checkpointer already has a `completed` approval for this
  `(run_id, name)`, replay that Decision instead of re-prompting.
- `edited_input` lets the human redirect; the agent uses it as the next input.

## Integration (small, surgical)
- **Recorder:** each checkpoint records an `approval` Step so it shows in
  `RunResult.steps` and the trace.
- **Agent.arun / SupervisorAgent.arun:** wrap the inner call so a
  `CheckpointRejected` is caught → recorded as `ErrorRecord(recovered=False,
  metadata={"reason": ...})` → returned as a partial `RunResult` (not a crash).
  Reuses the exact partial-failure path Module 8 already has; Agent gets a small
  equivalent.

## Testing (spec §8)
- Approval resumes the run (AutoApprove → agent proceeds, returns result).
- Rejection produces a controlled failure recorded in `RunResult.errors`
  (recovered=False, reason present) — the gate.
- `edited_input` is honoured (human redirect changes the next input).
- Console backend parses stdin y/n (monkeypatched input).
- Sync callback auto-wrapped; async callback awaited.
- Resume: a persisted approved Decision replays without re-prompting (temp
  SQLite, simulated restart).

## OOP / reuse
- **Abstraction:** `ApprovalBackend` protocol (console/callback/auto impls prove
  it earns its keep). **Reuse:** Module 8 `Checkpointer` (pause persistence),
  Module 7 recorder (`record_step`), Module 0 `ErrorRecord`, `AgentArgusError`.
- No methodoverload site — waived.

## Failure modes (for DESIGN_LOG)
- A backend that never returns (human never responds) blocks the run — async
  lets the caller impose a timeout; documented (a timeout wrapper is future).
- Console backend in a non-interactive context (CI) would hang on stdin — it
  detects a non-TTY and defaults to reject with a clear log, rather than hang.
- `edited_input` trusts the human — no validation of the redirect; documented.

## Gate
A run pauses at a checkpoint and resumes on approval; a rejection is recorded as
a controlled failure in `RunResult.errors` (not a crash); pending approval
persists/resumes via the checkpointer. Deterministic tests (auto backends) green;
ruff + mypy clean; ≥80% coverage; DESIGN_LOG + Context-block HARD_QUESTIONS +
module_notes/module9.md.
```
