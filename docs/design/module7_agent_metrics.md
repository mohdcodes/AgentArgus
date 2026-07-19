# Module 7 — Agent Metrics: Design

> Per-module design doc, written before implementation, shaped by the
> start-of-module answers. Approve before code.

## ⚠️ Hard requirement: ToolUseAccuracy needs an expected-tools label
`ToolUseAccuracy` measures "did the agent call the *right* tools?" — which is
**undefined without a ground truth**. The dataset author MUST supply the expected
tool names per case in `metadata["expected_tools"]` (exactly as they supply a
`reference` answer for ContextRecall). With no label the metric returns
**NOT_APPLICABLE** and is excluded from the scores — it never guesses what the
"correct" tool is. This requirement is stated in: the metric's docstring, this
design, DESIGN_LOG, the dataset docs, and a raised-visibility log line when the
metric is asked to score a case that lacks the label.

Example dataset case (Deep Research agent):
```jsonl
{"question": "What was Tesla's revenue growth in 2023?",
 "reference": "Tesla's 2023 revenue grew ~19% to $96.8B.",
 "metadata": {"expected_tools": ["web_search", "fetch_page", "calculator"]}}
```
*Actual* tools come from the recorder (`record_tool_call(...)` inside the agent).
Score = F1 of expected-name-set vs. actual-name-set.

## Goal (spec §6.5 agent metrics, gate §5)
Metrics that judge *agent behaviour* (not text quality), reading the
spans/steps/tool_calls/errors the earlier modules produce. New `Metric`
subclasses → they drop into the existing `EvalSuite`/`EvalRunner`, so RAG **and**
agent metrics run in one unified suite (the gate). Also solves the standing gap:
`tool_calls`/`steps` were always empty — Module 7 adds the recorder that fills
them.

## Decisions locked (start-of-module answers)
1. **Two tool metrics, resolving "how do we judge correct tool use?":**
   - `ToolUseAccuracy` (**A** — "did it call the *right* tools?") = F1 of
     expected tool names (from the dataset case) vs. actual. NOT_APPLICABLE if no
     `expected_tools` label. Deterministic, no judge.
   - `ToolSuccessRate` (**B** — "did the tools *work*?") = successful ToolCalls /
     total. Works on any run, zero setup. Reads the `success` flag already on
     `ToolCall`.
2. **`ErrorRecoveryRate`** = recovered errors / total errors; **1.0 if no errors**
   (nothing to recover = perfect). Reads Module 4's `recovered` flag.
3. **`PlanCoherence`** = LLM judge over `steps` (0–1); **NOT_APPLICABLE if no
   steps** (can't judge a plan that wasn't recorded).
4. **Recorder:** a lightweight object the inner agent writes to
   (`record_tool_call`, `record_step`); `Agent.arun` collects them onto the
   `RunResult`. Framework-agnostic, opt-in.
5. **Data flow mirrors RAG:** *expected* labels come from the dataset case
   (`metadata["expected_tools"]`), *actual* behaviour from the recorder — exactly
   like `reference`/`answer`.

## Files
```
agentargus/eval/metrics/agent.py     # ToolUseAccuracy, ToolSuccessRate,
                                      #   PlanCoherence, ErrorRecoveryRate
agentargus/agents/recorder.py        # Recorder (contextvar-based) + record_* API
agentargus/agents/agent.py           # collect recorder output onto RunResult
tests/unit/test_agent_metrics.py
tests/unit/test_recorder.py
```

## The Recorder (`agents/recorder.py`)
```python
class Recorder:
    def record_tool_call(self, name, args, result, success=True, latency=0.0, error=None)
    def record_step(self, kind, content, **metadata)
    @property tool_calls -> tuple[ToolCall, ...]
    @property steps -> tuple[Step, ...]

# contextvar so the inner fn can record without threading an object through:
_current_recorder: ContextVar[Recorder | None]
def current_recorder() -> Recorder | None      # inner fn calls this
def record_tool_call(...) / record_step(...)    # module-level convenience
```
- `Agent.arun` sets a fresh `Recorder` in the contextvar for the run (same
  pattern as `set_trace_id`), then after the inner call reads
  `recorder.tool_calls` / `recorder.steps` onto the `RunResult`.
- Async-safe (contextvars propagate into tasks). Sync-inner-in-thread caveat is
  the same as trace_id (documented) — the convenience functions also accept an
  explicit recorder for the thread case.

## The metrics (`eval/metrics/agent.py`) — inherit `Metric` / `LLMJudgeMetric`
- **`ToolSuccessRate(Metric)`** — no judge. `sum(tc.success) / len(tool_calls)`;
  NOT_APPLICABLE if no tool calls. (A pure-heuristic metric — proves the `Metric`
  ABC earns its keep without an LLM, per checkpoint 6.1.)
- **`ToolUseAccuracy(Metric)`** — no judge. Expected names from
  `metadata["expected_tools"]`; actual = `{tc.name}`. F1 = 2PR/(P+R) over the
  sets. NOT_APPLICABLE if no expected list.
- **`ErrorRecoveryRate(Metric)`** — no judge.
  `sum(e.recovered) / len(errors)`; 1.0 if no errors.
- **`PlanCoherence(LLMJudgeMetric)`** — judge rates the ordered `steps` 0–1
  (`{"coherence": 0..1}`); NOT_APPLICABLE if no steps.

`MetricInput` gains agent fields (tool_calls, steps, errors, expected_tools) via
extension of `_from_run_result` / `_from_dict` — the overload site #4 stays the
same shape, just richer extraction.

## MetricInput extension
```python
@dataclass(frozen=True)
class MetricInput:
    question, answer, contexts, reference          # (Module 5)
    tool_calls: tuple[ToolCall, ...] = ()
    steps: tuple[Step, ...] = ()
    errors: tuple[ErrorRecord, ...] = ()
    expected_tools: tuple[str, ...] = ()
```
`_from_run_result` pulls tool_calls/steps/errors from the RunResult and
expected_tools from metadata; `_from_dict` pulls all from the test dict.

## Testing (spec §8)
- `ToolSuccessRate`: all-success → 1.0; mixed → fraction; no calls → N/A.
- `ToolUseAccuracy`: exact match → 1.0; partial → correct F1; no expected → N/A.
- `ErrorRecoveryRate`: all recovered → 1.0; none → 0.0; no errors → 1.0.
- `PlanCoherence`: fake judge high/low; no steps → N/A.
- Recorder: record_tool_call/step land on RunResult after a run; contextvar
  isolation across two runs.
- Unified suite: a mixed `EvalSuite([Faithfulness, ToolSuccessRate,
  ErrorRecoveryRate])` runs and returns all applicable scores (the gate).

## OOP / reuse
- Reuses `Metric`/`LLMJudgeMetric`/overload site #4 (Module 5), `EvalSuite`
  (Module 5), `RunResult` fields (Module 0), Module 4's `recovered` flag.
- Recorder mirrors the `set_trace_id` contextvar pattern (Module 0/1).
- The two non-LLM metrics are the concrete "non-judge Metric" that proves the
  `Metric` abstraction isn't LLM-specific (checkpoint 6.1).

## Failure modes (for DESIGN_LOG)
- Sync inner fn in a worker thread won't see the recorder contextvar (same as
  trace_id) — documented; explicit-recorder escape hatch provided.
- ToolUseAccuracy F1 is name-set based — ignores call order and arguments
  (order/args-sensitive matching is a documented future option).
- PlanCoherence judge subjectivity — same calibration caveat as the RAG metrics.

## Gate
A unified `EvalSuite` mixing RAG + agent metrics runs over a run/dataset and
returns scores for all applicable metrics; recorder-fed tool_calls/steps appear
on the RunResult; mock-judge + deterministic tests green; ruff + mypy clean;
≥80% coverage; DESIGN_LOG + Context-block HARD_QUESTIONS + module_notes/module7.md.
```
