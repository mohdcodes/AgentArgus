# AgentArgus — HARD_QUESTIONS

Questions a skeptical staff engineer / interviewer would ask about each module.
**Claude Code writes the questions; the owner writes the answers.** A module's
gate does not close until the owner can answer its batch in their own words.

---

## Module 9 — Human-in-the-Loop

> Context-block format: Deep Research Agent example + code citation + Claude
> Code's own answer. Read it, then write yours below.

### 1. Rejection raises — how is that not just a crash?

**Context:**
- *Research-agent example:* a human denies the expensive 50-page crawl.
- *Code:* `Checkpoint.require_approval` does `raise CheckpointRejected(...)`;
  `Agent.arun` catches it → `ErrorRecord(recovered=False)` + partial RunResult
  with `metadata["failed"]=True`.
- *My answer:* the raise is an *internal* signal; the run loop converts it to a
  first-class outcome. The caller gets `result.output is None`,
  `result.metadata["failed"]`, and `result.errors[0].reason` — a normal,
  inspectable result, not an unhandled exception. The user places the gate;
  AgentArgus owns the denial handling. Same "controlled failure" posture as
  Module 8's worker failures.

*Your answer:*

### 2. Async-first backend with a sync adapter — why?

**Context:**
- *Research-agent example:* the approval callback posts to Slack and awaits a
  human clicking a button minutes later.
- *Code:* `ApprovalBackend.decide` is async; `CallbackApprovalBackend` awaits a
  coroutine fn or runs a plain fn via `to_thread`.
- *My answer:* a real approval waits on network I/O (a human over Slack/UI), which
  must not block the event loop — so async is the native shape. But many users
  have a simple sync callback; auto-wrapping it in `to_thread` means they don't
  have to learn async. Async-first with a sync on-ramp, consistent with the
  async-core decision from Module 1.

*Your answer:*

### 3. edited_input — why let the human change the agent's action?

**Context:**
- *Research-agent example:* the agent proposes crawling 50 pages; the reviewer
  approves but edits it down to 10.
- *Code:* `Decision.edited_input`; the agent uses it as the next input when set.
- *My answer:* real HITL is rarely pure yes/no — reviewers correct/redirect. A
  bare bool forces "reject and restart" for a tweak that approve-with-edit
  handles in one step. The cost: `edited_input` is trusted (we don't validate the
  human's redirect) — documented; validation is the agent's job if it matters.

*Your answer:*

### 4. Explicit checkpoint placement vs. auto-gating every tool call

**Context:**
- *Research-agent example:* you want approval before the *crawl*, not before
  every `web_search`.
- *Code:* the agent calls `await checkpoint(context)` exactly at the crawl.
- *My answer:* auto-gating every tool call would be unusably chatty (approve 40
  searches?) and would couple HITL to the recorder. Explicit placement gates the
  one action that matters — precise, opt-in, and the checkpoint is just a call
  the agent makes where it decides, like `record_tool_call`. Rejected the
  auto-gate for exactly this.

*Your answer:*

### 5. Pause/resume — how does an hours-long approval not block or lose state?

**Context:**
- *Research-agent example:* approval sits in a queue for 3 hours; the process
  restarts in between.
- *Code:* `require_approval` persists a `pending`/`approved` step via the Module 8
  `Checkpointer`; `_replay_if_approved` returns a prior approval without
  re-prompting.
- *My answer:* two parts — async means awaiting the decision doesn't block other
  work; the checkpointer means an approval already granted (even before a
  restart) is replayed from SQLite instead of re-asking. Reuses Module 8's exact
  durable-resume mechanism; HITL added almost no new persistence code.

*Your answer:*

### 6. Console backend in CI would hang on stdin — how handled?

**Context:**
- *Research-agent example:* a nightly eval job (no human, no TTY) hits a console
  checkpoint.
- *Code:* `ConsoleApprovalBackend.decide` checks `sys.stdin.isatty()`; non-TTY →
  logs + returns `Decision(approved=False)`.
- *My answer:* fail-safe, not fail-hang. An unattended context can't approve, so
  the safe default is reject-with-a-clear-reason rather than block forever on
  `input()`. Same fail-safe instinct as the cost-ceiling and the circuit breaker
  — when unsure, stop safely and say why.

*Your answer:*

### 7. What travels in the approval `context`?

**Context:**
- *Research-agent example:* the reviewer needs to see "50-page crawl, est
  \$2.50" to decide.
- *Code:* `require_approval(context)` takes an arbitrary `Mapping`; the agent
  fills it (proposed action, cost, accumulated state).
- *My answer:* the agent decides what the human needs to see — the proposed
  action, its estimated cost/impact, relevant state — passed as a freeform dict.
  Freeform because what's decision-relevant varies by checkpoint; it's persisted
  and logged, so it doubles as the audit record of *what* was being approved.

*Your answer:*

### 8. HITL is almost pure composition — what did it actually add?

**Context:**
- *Research-agent example:* pausing for approval reuses the checkpointer (M8),
  the recorder (M7), ErrorRecord (M0).
- *Code:* `hitl/checkpoint.py` imports `record_step`, the `Checkpointer`,
  `CheckpointRejected`; the only new integration is one `except` in `Agent.arun`.
- *My answer:* the genuinely new bits are small — the `Decision`/`ApprovalBackend`
  abstraction and the rejection-as-controlled-failure wiring. Persistence, step
  recording, and error surfacing all already existed. That HITL slotted in with
  one catch clause is evidence the earlier seams generalize; a feature that
  needed new persistence + new error machinery would have signalled otherwise.

*Your answer:*

### 9. Multiple checkpoints in one run — do they collide in storage?

**Context:**
- *Research-agent example:* a run gates both a "crawl" and a "publish" action —
  two named checkpoints.
- *Code:* `_step_key` hashes the checkpoint `name` into a step slot; persistence
  rows are `worker="checkpoint:<name>"`.
- *My answer:* each checkpoint persists under its own name, so "crawl" and
  "publish" occupy distinct slots and replay independently. The hash-based step
  key is a pragmatic slot within the run's checkpoint table. Honest limitation:
  two checkpoints with the *same* name in one run would share a slot — documented;
  name them distinctly.

*Your answer:*

### 10. Is a raised rejection catchable by the reliability layer (retry)?

**Context:**
- *Research-agent example:* the agent is wrapped in a `ReliabilityPolicy` with
  retries, and a checkpoint is rejected.
- *Code:* `CheckpointRejected` is an `AgentArgusError`; retry's default retryable
  set is transient/network types only.
- *My answer:* it should NOT be retried — a human said no; re-running and
  re-asking would be wrong (and annoying). Because `CheckpointRejected` isn't in
  the retryable set, `RetryWithBackoff` re-raises it immediately, and the Agent's
  catch turns it into the controlled failure. The retryable-set design (Module 4)
  pays off here: a deliberate denial isn't mistaken for a transient blip.

*Your answer:*

---

## Module 8 — Orchestration Patterns (production-grade)

> Context-block format: Deep Research Agent example + code citation + Claude
> Code's own answer. Read it, then write yours below.

### 1. Crash mid-step — how do you avoid trusting a half-done step?

**Context:**
- *Research-agent example:* the process is killed while the `calc` worker is
  running on step 2 of a retrieval→calc→synth chain. On restart, step 2 must NOT
  be treated as done.
- *Code:* `SqliteCheckpointer.save_step` writes `STATUS_RUNNING` before the
  worker runs and `STATUS_COMPLETED` only after it returns; `last_completed_step`
  / `completed_output` consider only `completed` rows.
- *My answer:* the `status` column is the crash-safety signal. A step is
  `running` from before-worker to after-worker (committed transactions); killed
  mid-flight it stays `running`, so on resume it's re-run, never replayed as
  cached. Only `completed` steps are trusted. WAL + per-step commit make the
  transitions durable.

*Your answer:*

### 2. Why is Handoff a return value, not an exception?

**Context:**
- *Research-agent example:* retrieval wants synthesis next; it `return
  Handoff("synthesis", docs)`. Synthesis just `return "answer"`.
- *Code:* `SupervisorAgent.arun` checks `isinstance(current, Handoff)` — Handoff
  continues, anything else is final.
- *My answer:* exceptions are for errors, not normal control flow. Using
  `raise Handoff` would (a) misuse exceptions and (b) collide with the Module 4
  reliability layer, which catches exceptions to retry — a handoff would look
  like a failure to retry. Returning a Handoff keeps control flow explicit and
  keeps orchestration composable with reliability.

*Your answer:*

### 3. SupervisorAgent is-a BaseAgent — what does that unlock?

**Context:**
- *Research-agent example:* you want to trace, cost, and eval the WHOLE
  multi-agent system, not each worker separately.
- *Code:* `class SupervisorAgent(BaseAgent)`; test wraps it: `Agent(sup)`.
- *My answer:* because the supervisor is a `BaseAgent`, `Agent(supervisor,
  tracer=..., cost=...)` wraps the entire orchestration as one unit — spans,
  cost, RunResult, eval metrics all apply to the system as a whole. And it can
  itself be a worker of another supervisor (nesting). This is the is-a payoff:
  the whole earlier stack composes over a multi-agent system for free.

*Your answer:*

### 4. Polymorphism — prove the supervisor doesn't know worker types

**Context:**
- *Research-agent example:* today workers are plain `Agent`s; tomorrow one is a
  `SupervisorAgent` or a LangGraph-wrapped agent.
- *Code:* `SupervisorAgent` stores `dict[str, BaseAgent]` and calls
  `self._workers[name].arun(input)`.
- *My answer:* it only ever touches `BaseAgent.arun` — never a concrete type or
  isinstance branch. Any `BaseAgent` (Agent, another SupervisorAgent, a custom
  subclass) is a valid worker with zero supervisor changes. This is inheritance-
  based polymorphism; it's the concrete reason `BaseAgent` was defined as an ABC
  back in Module 1.

*Your answer:*

### 5. Concurrency — two runs sharing one SQLite file

**Context:**
- *Research-agent example:* your service handles query A and query B
  concurrently, both checkpointing to the same `checkpoints.db`.
- *Code:* every query is `WHERE run_id = ?`; writes are under a `threading.Lock`;
  WAL enabled; `check_same_thread=False`.
- *My answer:* run_id scoping means A and B never see each other's steps even in
  one file. The lock serialises writes (SQLite is single-writer anyway); WAL lets
  reads proceed concurrently with a write. Under very heavy parallel writes SQLite
  contention is real — documented; a Postgres/Redis Checkpointer could drop in via
  the ABC if needed.

*Your answer:*

### 6. Worker failure returns a partial RunResult — why not raise?

**Context:**
- *Research-agent example:* the `calc` worker throws on a bad number mid-chain.
- *Code:* `arun`'s `try/except` records an `ErrorRecord`, checkpoints `failed`,
  optionally DLQs, and returns `_assemble(failed=True)`.
- *My answer:* production graceful degradation — the caller gets a RunResult that
  says what ran, what failed, and why (chain-so-far + ErrorRecord), instead of a
  bare traceback that loses all context. It composes with reliability: wrap the
  worker in a `ReliabilityPolicy` and transient failures get retried *before*
  they ever reach this partial-failure path.

*Your answer:*

### 7. The context-size cap — what's it defending against?

**Context:**
- *Research-agent example:* a buggy chain keeps appending full documents to
  `Handoff.context` on every hop; after 50 hops it's hundreds of MB.
- *Code:* `_check_context_size` serialises the context and raises
  `OrchestrationError` past ~1 MB.
- *My answer:* `max_steps` bounds the *number* of hops; the context cap bounds
  the *size* carried across hops — a different runaway (memory, not loop). Without
  it a long chain that accumulates state could OOM the process. Both are
  configurable guards that fail loud rather than degrade mysteriously.

*Your answer:*

### 8. LLM router picks a name that isn't a worker — then what?

**Context:**
- *Research-agent example:* the judge replies "use the retrieval agent" instead
  of the bare name `retrieval`, or hallucinates `researcher`.
- *Code:* `LLMRouter.route` first tries an exact match, then looks for a known
  name *inside* the reply; `SupervisorAgent._route` raises `OrchestrationError`
  if the final pick still isn't a worker.
- *My answer:* two layers — the router is tolerant of prose ("use the retrieval
  agent" → finds `retrieval`), and the supervisor validates the final choice
  against the worker set, failing loud on a genuine hallucination rather than
  silently misrouting or picking a default. A silent misroute is the worst
  outcome (wrong answer, no signal); this makes it impossible.

*Your answer:*

### 9. Resume replays cached outputs — is that always safe?

**Context:**
- *Research-agent example:* step 0 (retrieval) completed and was checkpointed;
  the run resumes and replays retrieval's cached output instead of re-searching.
- *Code:* on resume, `arun` returns `completed_output(run_id, step)` for
  already-completed steps without calling the worker.
- *My answer:* safe for *deterministic-enough* resume where re-running a step
  would waste work/money (a re-search costs an API call). The caveat: if the
  world changed between the original run and the resume, the cached output is
  stale — acceptable for the "crash then resume promptly" use case this targets.
  A future `force_rerun` flag could bypass the cache; documented.

*Your answer:*

### 10. All hardening reused existing seams — coincidence or design?

**Context:**
- *Research-agent example:* production observability = spans (Module 2) + logs
  (Module 0); failure handling = DLQ (Module 4) + ErrorRecord (Module 0);
  chain visibility = record_step (Module 7).
- *Code:* `patterns.py` imports the tracer seam, `record_step`, `ErrorRecord`,
  and accepts a `dead_letter` sink — no new infrastructure.
- *My answer:* by design. Every prior module built a reusable seam, so making
  orchestration production-grade was *composition*, not new plumbing — spans,
  logs, DLQ, error records, step recording all already existed. If Module 8 had
  needed all-new observability/failure machinery, that would have signalled the
  earlier abstractions weren't general enough. They were.

*Your answer:*

---

## Module 7 — Agent Metrics

> Context-block format: Deep Research Agent example + code citation + Claude
> Code's own answer. Read it, then write yours below.

### 1. "How do you judge correct tool use?" — the split

**Context:**
- *Research-agent example:* the agent should call `web_search`, `fetch_page`,
  `calculator` for a revenue-growth question. How do you score whether it did?
- *Code:* `ToolUseAccuracy` (F1 vs. `metadata["expected_tools"]`, N/A if absent)
  and `ToolSuccessRate` (successes/total) in `eval/metrics/agent.py`.
- *My answer:* "correct" needs a ground truth, so `ToolUseAccuracy` requires an
  author-labeled expected list and abstains (NOT_APPLICABLE) without one — it
  never guesses. `ToolSuccessRate` answers a *different*, always-available
  question ("did the calls work?"). Splitting them was the resolution to the
  "how can we possibly know the right tool?" problem: the dataset author knows;
  we don't invent it.

*Your answer:*

### 2. ToolUseAccuracy requires a label — is abstaining the right call?

**Context:**
- *Research-agent example:* you run 50 questions but only labeled `expected_tools`
  on 20 of them.
- *Code:* `ToolUseAccuracy._score` returns `NOT_APPLICABLE` (logged at INFO) when
  `expected_tools` is empty; `EvalSuite` drops NaN.
- *My answer:* abstaining is honest — scoring the 30 unlabeled cases against an
  empty expected set would either crash or produce a meaningless number. You get
  accuracy on the 20 you labeled and nothing fabricated for the rest. Same
  posture as ContextRecall-without-reference.

*Your answer:*

### 3. Name-set F1 ignores order and arguments — a hole?

**Context:**
- *Research-agent example:* the agent calls `fetch_page` BEFORE `web_search`
  (nonsensical), or calls `calculator("wrong expr")` — both still score 1.0 on
  tool selection.
- *Code:* `ToolUseAccuracy._score` compares `set(expected)` vs.
  `{tc.name for tc in tool_calls}`.
- *My answer:* yes, it's a deliberate v0.1.0 scope — it measures *selection*
  (the most common failure: didn't call the tool at all / called a wrong one),
  not ordering or argument correctness. Documented in the docstring + DESIGN_LOG
  as a known limitation with order/arg-aware matching as the next step. Better a
  clearly-scoped metric than a vaguely-comprehensive one.

*Your answer:*

### 4. The Recorder + sync inner functions — why does it work when trace_id doesn't?

**Context:**
- *Research-agent example:* a plain sync `research(q)` function calls
  `record_tool_call(...)`; it runs in a `to_thread` worker, yet the calls appear
  on the RunResult.
- *Code:* `Agent.arun` sets a `Recorder` in a contextvar before the inner call;
  sync inner runs via `asyncio.to_thread`.
- *My answer:* `to_thread` *copies* the context into the worker, so the worker
  sees the same `Recorder` object. Because the recorder is *mutable* and we
  *append* to it (never rebind the var), those appends land on the instance
  `arun` reads back. trace_id differs because it's a *value* rebind
  (`set_trace_id` in the worker wouldn't propagate out). Verified live.

*Your answer:*

### 5. ErrorRecoveryRate = 1.0 when there are no errors — defensible?

**Context:**
- *Research-agent example:* a clean run with zero failures. What's its recovery
  rate?
- *Code:* `ErrorRecoveryRate._score` returns 1.0 if `not inp.errors`.
- *My answer:* "recovery rate" = "of the failures that happened, how many were
  recovered." Zero failures = nothing to recover = a perfect 1.0 (you can't do
  better than not failing). The alternative (NOT_APPLICABLE) would drop the
  metric for healthy runs, which is worse — you'd lose the signal that the run
  was clean. Contrast ToolUseAccuracy where absence is a *missing label*, not a
  *good outcome*.

*Your answer:*

### 6. Three of four agent metrics use no LLM — why does that matter?

**Context:**
- *Research-agent example:* scoring tool success / recovery on a run offline, in
  CI, with no API key.
- *Code:* `ToolUseAccuracy`, `ToolSuccessRate`, `ErrorRecoveryRate` inherit
  `Metric` (not `LLMJudgeMetric`) and call no judge.
- *My answer:* they prove the `Metric` ABC isn't secretly an "LLM metric"
  interface — the abstraction genuinely spans heuristic and LLM metrics
  (checkpoint 6.1 asked for exactly a non-LLM concrete metric). Practically: they
  run free, fast, and deterministically in CI, unlike the judge metrics.

*Your answer:*

### 7. PlanCoherence NOT_APPLICABLE vs. 0.0 for no steps

**Context:**
- *Research-agent example:* a simple agent that returns an answer without
  recording any reasoning steps.
- *Code:* `PlanCoherence._score` returns `NOT_APPLICABLE` if `not inp.steps`.
- *My answer:* no recorded steps means "no plan to judge," not "a bad plan."
  Scoring 0.0 would defame agents that simply don't emit steps (many don't). N/A
  excludes it honestly. This is the same "missing data ≠ bad result" principle as
  ContextRecall.

*Your answer:*

### 8. Expected tools live on the dataset case, not the Agent — why?

**Context:**
- *Research-agent example:* the RIGHT tools for "Tesla revenue growth" (needs a
  calculator) differ from "who wrote Hamlet" (doesn't).
- *Code:* `_from_run_result` reads `metadata["expected_tools"]`; the runner
  merges the case's expected_tools in.
- *My answer:* the correct tool set is *per question*, not per agent — a global
  "available tools" registry on the Agent can't say which are right for THIS
  case. So the label belongs on the dataset case (like the reference answer),
  and the runner bridges it into scoring metadata. This is why we rejected
  "register tools on the Agent."

*Your answer:*

### 9. MetricInput grew agent fields — did the overload site change?

**Context:**
- *Research-agent example:* the same `compute(run_result_or_dict)` now feeds both
  RAG and agent metrics.
- *Code:* `MetricInput` gained tool_calls/steps/errors/expected_tools; the
  `@overload`s on `compute` (RunResult / dict) are unchanged, only `_from_*`
  extraction got richer.
- *My answer:* the overload contract is stable — richer extraction, same two
  branches. RAG metrics ignore the new fields, agent metrics ignore the RAG
  fields; each reads what it needs from one shared normalised input. No new
  dispatch site, no changed signatures.

*Your answer:*

### 10. Recorder is opt-in — what if the agent never records?

**Context:**
- *Research-agent example:* a user wraps an agent that doesn't call
  `record_tool_call` at all.
- *Code:* `Agent.arun` still binds a `Recorder`; if nothing records,
  `tool_calls`/`steps` are empty tuples.
- *My answer:* opt-in is correct — AgentArgus can't magically know what tools an
  arbitrary callable invoked (it's framework-agnostic). No recording → empty
  tool_calls → ToolSuccessRate/ToolUseAccuracy report NOT_APPLICABLE, honestly
  saying "no tool data" rather than fabricating. The cost of framework-agnosticism
  is that behaviour metrics need the user to emit the signal; the recorder makes
  that one function call.

*Your answer:*

---

## Module 6 — Eval: Dataset + Runner + Report

> Context-block format: Deep Research Agent example + code citation + Claude
> Code's own answer. Read it, then write yours below.

### 1. Concurrency cap — why 8, and what breaks without it?

**Context:**
- *Research-agent example:* eval a 50-question dataset; each case = 1 agent run +
  up to 4 judge calls = ~250 LLM calls.
- *Code:* `EvalRunner.arun` wraps each case in `async with asyncio.Semaphore(8)`
  and `asyncio.gather`s them.
- *My answer:* unbounded `gather` would fire all 50 cases (250 calls) at once →
  provider rate-limit/429 storms. The semaphore caps in-flight cases at 8, fast
  but polite. Without it you'd need the reliability layer just to survive your
  own eval. 8 is a conservative default; configurable per runner.

*Your answer:*

### 2. One failing case — why capture instead of raise?

**Context:**
- *Research-agent example:* case #37 of 50 throws (a malformed query crashes the
  agent). You still want the other 49 scored.
- *Code:* `EvalRunner._eval_one` wraps the run in `try/except`, returning a
  `CaseResult(result=None, error=...)` on failure.
- *My answer:* an eval batch is a measurement, not a transaction — losing 49
  good scores because 1 case crashed is the wrong trade. We capture the error on
  that case (visible in the report, `_failures` count, red row in HTML) and press
  on. Contrast with `Agent.run`, where a single run's failure IS the result.

*Your answer:*

### 3. The scoring view — why merge case fields into metadata?

**Context:**
- *Research-agent example:* the dataset case has the ground-truth `reference`;
  the agent's `RunResult` doesn't know about it. ContextRecall needs it.
- *Code:* `EvalRunner._scoring_view` does `replace(result, metadata={**result.metadata, question, reference, contexts?})`.
- *My answer:* the metrics read inputs from `RunResult.metadata` (Module 5's
  convention), but the *dataset* holds the reference/expected-contexts. The
  runner bridges them by producing a scoring-view RunResult with the case fields
  merged in — without mutating the original run (immutability via `replace`).
  Agent-produced contexts win; the case's fill in only if the agent produced none.

*Your answer:*

### 4. Agent contexts win over case contexts — why that precedence?

**Context:**
- *Research-agent example:* the case ships pre-retrieved contexts for offline
  testing, but the live agent actually retrieved its own. Which does
  ContextPrecision score?
- *Code:* `_scoring_view` only uses `case.contexts` `if not md.get("contexts")`.
- *My answer:* if the agent retrieved, THOSE are what actually fed the answer, so
  they're what we should judge — scoring the case's stale pre-set contexts would
  measure the wrong thing. Case contexts are a fallback for offline/agent-doesn't-
  retrieve scenarios. Precedence encodes "score what really happened."

*Your answer:*

### 5. Regression threshold — noise vs. sensitivity

**Context:**
- *Research-agent example:* baseline faithfulness 0.85; today 0.82 (noise?) vs.
  0.72 (real drop?).
- *Code:* `EvalReport.regressions` flags when `current - baseline < -threshold`
  (default 0.05).
- *My answer:* 0.05 threshold treats 0.82 as noise (not flagged) and 0.72 as a
  regression (flagged). It's a deliberately blunt first-pass signal — configurable,
  and the documented upgrade is a bootstrap/t-test when the dataset is big enough
  for significance to mean something. A fixed threshold can miss a real 0.04 drop;
  that's the accepted trade for not crying wolf on variance.

*Your answer:*

### 6. Self-contained HTML — why inline everything?

**Context:**
- *Research-agent example:* you email the eval report to a teammate or attach it
  to a PR.
- *Code:* `report.html.j2` has inline `<style>`, no external CSS/JS/fonts.
- *My answer:* a report that needs a CDN or sibling asset breaks the moment it
  leaves your machine (offline, email attachment, CI artifact). Inlining makes it
  a single portable file that opens anywhere — the same self-containment
  principle the Artifact system uses. Cost: no shared stylesheet, but a report is
  a leaf document, not an app.

*Your answer:*

### 7. The Jinja template is a non-.py file — what did that require?

**Context:**
- *Research-agent example:* a user `pip install agentargus` and calls
  `report.to_html()` — the template must be present in the installed package.
- *Code:* `pyproject.toml` `[tool.hatch.build.targets.wheel].artifacts =
  ["agentargus/eval/templates/*.j2"]`; `report.py` loads it via
  `FileSystemLoader(Path(__file__).parent / "templates")`.
- *My answer:* wheels ship `.py` by default; a `.j2` asset would be silently
  omitted and `to_html` would `TemplateNotFound` at runtime for installed users
  (but pass in the dev tree — a nasty "works on my machine" bug). I declared it
  as a build artifact AND verified it's inside the built wheel with a zipfile
  check.

*Your answer:*

### 8. EvalRunner.run mirrors BaseAgent.run's loop-guard — coincidence?

**Context:**
- *Research-agent example:* calling `runner.run(...)` from a notebook that already
  has an event loop.
- *Code:* `EvalRunner.run` does the same `get_running_loop()` → raise pattern as
  `BaseAgent.run`.
- *My answer:* not a coincidence — both are sync drivers over async cores, so
  both face the "called inside a running loop" problem and answer it the same
  way (raise with a "use arun" hint, don't nest loops). It's a deliberately
  repeated pattern; arguably it could be extracted to a shared helper if a third
  sync-driver appears (rule of three — not yet).

*Your answer:*

### 9. summary() mixes metric means with _-prefixed meta keys — clean?

**Context:**
- *Research-agent example:* `summary()` returns `{"faithfulness": 0.8,
  "_cases": 50, "_failures": 1, "_total_cost_usd": 1.23}`.
- *Code:* meta keys are `_`-prefixed; `regressions` skips `name.startswith("_")`.
- *My answer:* one dict keeps summary a single object, and the `_` convention
  separates metrics from meta so regression logic never compares "_cases"
  against a baseline. Alternative (two separate dicts/fields) is arguably cleaner
  typing but splits "the summary" into pieces. The `_`-prefix is a pragmatic,
  documented convention — the same instinct as private attributes.

*Your answer:*

### 10. Report holds RunResults for every case — memory at scale?

**Context:**
- *Research-agent example:* a 10,000-case eval — the report retains every
  `CaseResult` (with its full `RunResult`: spans, cost, output).
- *Code:* `EvalReport.__init__(self, case_results: list[CaseResult])` keeps them
  all in memory.
- *My answer:* fine for the realistic v0.1.0 scale (dozens–hundreds of cases),
  and keeping full results enables the per-case HTML table + drill-down. At
  10k+ it would be memory-heavy — the documented path is streaming/aggregating
  incrementally (keep summary + a bounded sample of full results). Deferred:
  YAGNI until someone runs eval at that scale.

*Your answer:*

---

## Module 5 — Eval Metrics (RAG)

> Context-block format: Deep Research Agent example + code citation + Claude
> Code's own answer. Read it, then write yours below.

### 1. Modeling RAGAS vs. depending on it

**Context:**
- *Research-agent example:* you want faithfulness/context-precision scores on the
  synthesis answer, the same metrics RAGAS is famous for.
- *Code:* `eval/metrics/rag.py` implements the metrics from scratch, docstring
  credits RAGAS; `pyproject.toml` has no `ragas` dependency.
- *My answer:* we model RAGAS's published methodology (Apache-2.0, verified from
  their docs) but don't depend on `ragas` — it pulls LangChain + a heavy tree,
  against the single-package/minimal-dep bet, and RAGAS is literally the gap §1
  says we fill. We get battle-tested definitions without the coupling or the
  dependency weight. Trade-off: we maintain the implementations ourselves.

*Your answer:*

### 2. Faithfulness and judge bias

**Context:**
- *Research-agent example:* the synthesis answer makes 5 claims; a lenient judge
  might rubber-stamp all 5 as "supported," inflating faithfulness to 1.0.
- *Code:* `Faithfulness._score` prompts for `{"claims":[...],"supported":[...]}`
  and returns `sum(supported)/len(supported)`.
- *My answer:* we reduce bias by forcing *decomposition* — the judge must list
  discrete claims and rule on each, so the score is a ratio of many small
  checkable judgments, not one vague number. It doesn't eliminate bias;
  calibration against human labels is future work. Tests pin the high/low paths
  with a fake judge so the *arithmetic* is trusted even if the judge isn't.

*Your answer:*

### 3. AnswerRelevance needs embeddings — the fallback

**Context:**
- *Research-agent example:* you have a judge but no embedding model wired; you
  still want an answer-relevance number.
- *Code:* `AnswerRelevance._score` uses the injected `Embedder` (RAGAS's
  gen-questions + cosine method) if present, else `_score_with_judge` (a direct
  0–1 rating).
- *My answer:* RAGAS's real method needs embeddings (mean cosine of generated
  questions vs. the original). We ship no embedder (framework-agnostic), so we
  offer an optional `Embedder` protocol for the exact method and a judge-scored
  approximation when it's absent — documented as an approximation, not silently
  different.

*Your answer:*

### 4. ContextPrecision is rank-aware — why does order matter?

**Context:**
- *Research-agent example:* retrieval returns 3 chunks; the relevant one is
  ranked 1st in one run, 3rd in another. Same relevant/irrelevant set.
- *Code:* `ContextPrecision._score` computes Average Precision:
  `Σ (Precision@k · rel_k) / total_relevant`, treating `contexts` order as rank.
- *My answer:* a good retriever puts relevant chunks first. Rank-aware AP rewards
  that — relevant-at-rank-1 scores 1.0, relevant-at-rank-3 scores lower — where a
  flat relevant/total fraction can't tell them apart. Verified: same set, better
  ranking → higher score. This is why we implemented full AP, not a fraction.

*Your answer:*

### 5. ContextRecall needs ground truth — why NOT_APPLICABLE when absent?

**Context:**
- *Research-agent example:* you run the agent on a fresh query with no labeled
  "correct answer." ContextRecall has nothing to measure recall *against*.
- *Code:* `ContextRecall._score` returns `NOT_APPLICABLE` (NaN) if
  `inp.reference` is missing; `EvalSuite.run` drops NaN scores.
- *My answer:* recall is by definition "did we retrieve enough to cover the
  *truth*" — without a ground-truth reference there is no truth to cover, so any
  number would be fabricated. Returning NOT_APPLICABLE and excluding it is honest;
  the alternative (score against the agent's own answer) measures something else
  entirely (self-consistency) and would mislead.

*Your answer:*

### 6. Tolerant JSON parsing — masking a broken judge?

**Context:**
- *Research-agent example:* the judge model returns prose instead of JSON on one
  of 100 dataset rows.
- *Code:* `LLMJudgeMetric._ask_json` tries strict JSON, then a fenced block, then
  a lenient `{...}` search, then logs a WARNING and returns a conservative
  default.
- *My answer:* one flaky response shouldn't fail a 100-row batch, so we degrade
  gracefully. The risk is masking a *systematically* broken judge — mitigated by
  the WARNING (visible signal) and a *conservative* default (Faithfulness→1.0 on
  no-claims is vacuous, relevance→0.0). If every row warns, that's the tell.

*Your answer:*

### 7. compute() overload — RunResult vs dict, why both?

**Context:**
- *Research-agent example:* production scores a real `RunResult`; a unit test
  wants to check Faithfulness on `{"answer":..,"contexts":[..]}` without building
  a whole run.
- *Code:* `Metric.compute` is overload site #4 — `@overload` on `RunResult` and
  on `dict`, both → `MetricInput` → `_score`.
- *My answer:* the dict overload makes metrics trivially unit-testable (no
  RunResult scaffolding), while production uses the real object. Both normalise to
  one `MetricInput` so scoring logic isn't duplicated. Same methodoverload
  constraints as sites #2/#3 (no future-annotations, bare dict).

*Your answer:*

### 8. EvalSuite is polymorphic — prove it doesn't know concrete types

**Context:**
- *Research-agent example:* you evaluate with `[Faithfulness, ContextPrecision]`
  today and add a custom `ToolUseAccuracy` (Module 7) tomorrow with no suite
  change.
- *Code:* `EvalSuite.run` does `{m.name: m.compute(source) for m in self.metrics}`
  — it only knows the `Metric` interface.
- *My answer:* the suite calls `compute`/reads `name` through the ABC; it never
  branches on metric type. Adding a metric is adding a `Metric` subclass to the
  list — open/closed. This is inheritance-based polymorphism, distinct from the
  overload-based polymorphism inside `compute` (dispatch on input type).

*Your answer:*

### 9. Missing judge → raise, not NaN. Why?

**Context:**
- *Research-agent example:* someone writes `Faithfulness()` (forgot the judge)
  and adds it to a suite that runs over 100 rows.
- *Code:* `LLMJudgeMetric._require_judge` raises `ValueError` naming the metric.
- *My answer:* a metric with no judge is a *configuration* mistake, not a data
  condition — fail fast and loud with a fix hint, don't emit 100 NaNs that
  silently drop the metric from every result (you'd think you evaluated
  faithfulness when you didn't). Contrast with ContextRecall's NOT_APPLICABLE,
  which IS a legitimate data condition (no reference).

*Your answer:*

### 10. with_scores once vs. per-metric — the efficiency note

**Context:**
- *Research-agent example:* scoring one run on 4 metrics.
- *Code:* `EvalSuite.run` builds the full `dict` first; `score()` calls
  `result.with_scores(dict)` exactly once.
- *My answer:* `RunResult` is immutable, so each `with_scores` makes a new object.
  Applying it once with all 4 scores creates one new RunResult, not four — the
  "collect then apply once" pattern flagged back in Module 0's immutability
  decision. Per-metric `with_scores` would allocate N results and only keep the
  last.

*Your answer:*

---

## Module 4 — Reliability

> Context-block format: Deep Research Agent example + code citation + Claude
> Code's own answer. Read it, then write yours below.

### 1. Compose order — why breaker → fallback → retry (inner)?

**Context:**
- *Research-agent example:* the synthesis step calls `claude-opus-4-8` with
  retry(3) + fallback to `claude-sonnet-5` + a breaker. Opus is having a bad
  minute (intermittent 503s).
- *Code:* `ReliabilityPolicy._ordered_strategies` returns `[breaker, fallback,
  retry]` (outer→inner); `__call__` wraps `reversed()` so retry is innermost.
- *My answer:* retry innermost means we retry *each model* a few times before
  giving up on it and switching (opus×3, then sonnet×3), not retry-the-whole-
  fallback-chain. Breaker outermost means if opus's endpoint is already known-
  down, we fail fast without even trying. The rejected order (retry outermost)
  multiplies attempts (retry × fallback) and retries even when the breaker is
  open.

*Your answer:*

### 2. What gets retried — and why not everything?

**Context:**
- *Research-agent example:* retrieval raises `TimeoutError` (transient, worth
  retrying) on one call and `ValueError` (a bug in the query parser) on another.
- *Code:* `RetryWithBackoff` has `DEFAULT_RETRYABLE = (TransientError,
  TimeoutError, ConnectionError)`; a non-matching exception hits the
  `except BaseException` branch and re-raises after one attempt.
- *My answer:* retry only transient/network failures. Retrying a `ValueError`
  from a code bug wastes 3 attempts (and money) on something that will never
  succeed. The retryable set is user-overridable for cases we didn't anticipate.

*Your answer:*

### 3. CircuitBreaker thread safety — prove it

**Context:**
- *Research-agent example:* 20 concurrent synthesis calls all hit a failing
  endpoint at once; their `record_failure()` calls race.
- *Code:* `CircuitBreaker` guards every transition with `self.__lock`
  (`threading.Lock`); counters are name-mangled (`__consecutive_failures`).
  Test `test_thread_safe_record_failure` runs 8 threads × 1000 failures.
- *My answer:* yes, thread-safe. All reads/writes of the state + counters happen
  inside the lock, so concurrent `record_failure` can't interleave into a
  corrupt state. The lock matters because sync inner callables run on
  `to_thread` worker threads (async-core reality), so genuinely-concurrent
  access is real, not hypothetical.

*Your answer:*

### 4. Recovered failures still recorded — why keep the noise?

**Context:**
- *Research-agent example:* retrieval's attempt 1 and 2 fail transiently, attempt
  3 succeeds. The run "succeeded" — should the two failures show up anywhere?
- *Code:* `RetryContext.record_error(exc, recovered=True, attempt=n)` appends an
  `ErrorRecord` per failed attempt; `Agent.arun` puts them on
  `RunResult.errors`.
- *My answer:* yes, record them with `recovered=True`. Transient failures that
  were overcome are early-warning signals (a dependency degrading), and eval's
  `ErrorRecoveryRate` metric (Module 7) computes recovery rate directly from
  these records. Hiding them would blind both ops and eval.

*Your answer:*

### 5. Fallback is framework-agnostic — defend it

**Context:**
- *Research-agent example:* you fall back from `claude-opus-4-8` to a *local*
  model, or even to a canned-response function — not necessarily another LLM.
- *Code:* `FallbackChain.__init__` runs each alternative through
  `to_async_callable`; a fallback is any callable or `BaseAgent`.
- *My answer:* fallbacks are just callables, so the reliability layer never
  learns about "models" or providers — matching the framework-agnostic bet. The
  rejected "list of model names + factory" design would couple reliability to a
  provider notion. Cost: the user constructs the callable themselves, but that's
  one line and keeps the layer clean.

*Your answer:*

### 6. DeadLetterQueue behind an ABC — what does that buy?

**Context:**
- *Research-agent example:* in production you want dead-lettered queries to go to
  Redis for a replay worker; in tests you want them in memory.
- *Code:* `DeadLetterSink(ABC).append(record)`; `JsonlDeadLetterSink`,
  `InMemoryDeadLetterSink` implement it; `DeadLetterQueue` wraps any sink.
- *My answer:* the ABC is the abstraction pillar — swap JSONL→Redis→SQS with zero
  `DeadLetterQueue` changes, and tests use the in-memory sink with no file I/O.
  Second concrete impl proving the abstraction earns its keep: the in-memory sink
  already exists and is used in tests.

*Your answer:*

### 7. The seam gained an errors channel — how, without breaking Passthrough?

**Context:**
- *Research-agent example:* a plain agent with no reliability configured must
  still produce a `RunResult` (with empty errors); a policy-wrapped one fills
  errors.
- *Code:* `Agent.arun` reads `getattr(self._reliability, "last_errors", ())` —
  duck-typed. `ReliabilityPolicy` exposes `last_errors`; `PassthroughReliability`
  does not, so it yields `()`.
- *My answer:* duck-typing keeps the null-object seam intact — I didn't have to
  add a `last_errors` to `PassthroughReliability`. This is the same seam-
  evolution pattern as Module 3's `table()`: the Agent asks for an optional
  capability and degrades gracefully when it's absent.

*Your answer:*

### 8. to_async_callable was extracted — what duplication did it kill?

**Context:**
- *Research-agent example:* both `Agent(retrieval_fn)` and a fallback list
  `[local_model_fn]` need the same sync-vs-async-vs-BaseAgent handling.
- *Code:* `_internal/callables.py:to_async_callable` is now used by both
  `Agent.wrap`'s object branch and `FallbackChain.__init__`.
- *My answer:* before Module 4, only `Agent.wrap` normalised targets. Fallback
  needed the identical logic. Rather than copy the `iscoroutinefunction` /
  `to_thread` / `BaseAgent.arun` handling, I extracted it — one home, both import
  it (the spec's "one behaviour, one home" rule).

*Your answer:*

### 9. Terminal failure: DLQ then re-raise. Why not return a RunResult?

**Context:**
- *Research-agent example:* a query fails retry AND fallback AND trips the
  breaker — everything is exhausted.
- *Code:* `ReliabilityPolicy.__call__` calls `self._dlq.put(...)` then `raise`;
  `Agent.arun` currently lets that propagate.
- *My answer:* v0.1.0 records the input to the DLQ and re-raises, so the caller
  sees a real exception (fail loud, don't swallow). A future refinement (noted in
  DESIGN_LOG §5) could turn a terminal failure into a `RunResult` with
  `recovered=False` errors instead of raising — deliberately deferred; the gate
  is the *recovered* path.

*Your answer:*

### 10. Backoff uses random jitter — is that a test-determinism problem?

**Context:**
- *Research-agent example:* CI must not flake because a retry slept a random
  amount, and tests must not actually wait seconds.
- *Code:* `RetryWithBackoff(sleep=...)` takes an injectable sleeper; tests pass
  `_nosleep`. Jitter uses `random.random()` only to vary the *delay value*, which
  tests ignore.
- *My answer:* no determinism problem — tests inject a no-op sleeper, so the
  random delay is computed but never waited on, and the retry *count*/behaviour
  (what tests assert) is fully deterministic. Real runs get jittered backoff to
  avoid thundering-herd retries.

*Your answer:*

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
