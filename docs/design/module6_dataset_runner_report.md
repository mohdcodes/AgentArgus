# Module 6 — Dataset + Runner + Report: Design

> Per-module design doc, written before implementation, shaped by the
> start-of-module answers. Approve before code.

## Goal (spec §6.5, gate §5)
Close the eval loop: batch an agent over an `EvalDataset`, score each case with a
Module 5 `EvalSuite`, and produce an `EvalReport` with regression detection and a
self-contained HTML render. **methodoverload site #1** (`EvalDataset.load`) lands
here.

## Decisions locked (start-of-module answers)
1. **Case schema:** `{question (req), reference?, contexts?, metadata?}`. The
   agent produces the answer (and possibly contexts) at run time; `reference`
   enables ContextRecall.
2. **Runner concurrency:** `asyncio.gather` with a configurable semaphore cap
   (default 8) over `agent.arun` — the async-core payoff.
3. **Regression:** per-metric mean drop beyond a threshold (default 0.05).
4. **Report:** one self-contained Jinja HTML (summary + per-case table +
   highlighted regressions); `summary()` also returns a dict.
5. **`load` site #1:** `str`=path (`.jsonl`/`.json` by extension), `list`=records,
   `dict`=single record.
6. **Case→metric wiring:** the runner merges the case's question/reference/
   contexts into a scoring view (metadata) before scoring; agent-produced
   contexts win over the case's pre-set contexts.

## Files
```
agentargus/eval/
├── dataset.py         # EvalCase, EvalDataset (load = overload site #1)
├── runner.py          # EvalRunner (async gather + cap), CaseResult
├── report.py          # EvalReport (summary/regressions/to_html)
└── templates/report.html.j2
tests/unit/test_dataset.py
tests/unit/test_runner.py
tests/unit/test_report.py
tests/unit/test_overload_sites.py   # + site #1
tests/fixtures/golden_dataset.jsonl
```

## EvalDataset (`dataset.py`) — methodoverload site #1
```python
@dataclass(frozen=True)
class EvalCase:
    question: str
    reference: str | None = None
    contexts: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = {}

class EvalDataset:
    cases: tuple[EvalCase, ...]
    @overload
    def load(self, source: str) -> EvalDataset: ...   # path (.jsonl / .json)
    @overload
    def load(self, source: list) -> EvalDataset: ...  # in-memory records
    @overload
    def load(self, source: dict) -> EvalDataset: ...  # single record
    @classmethod
    def from_jsonl(cls, path) -> EvalDataset: ...
```
- `dataset.py` omits `from __future__ import annotations`; overloads use bare
  `str`/`list`/`dict` (the recurring methodoverload constraints).
- Validation: each record must have a non-empty `question`; malformed rows raise
  a clear `ConfigError` naming the row index.

## EvalRunner (`runner.py`)
```python
@dataclass(frozen=True)
class CaseResult:
    case: EvalCase
    result: RunResult
    scores: dict[str, float]

class EvalRunner:
    def __init__(self, concurrency: int = 8): ...
    async def arun(self, agent, dataset, suite) -> EvalReport: ...
    def run(self, agent, dataset, suite) -> EvalReport: ...  # sync driver
```
- Per case: `result = await agent.arun(case.question)`; build a **scoring view**
  = `result` with `metadata` merged from the case (question, reference, and
  contexts if the agent didn't produce its own); `scores = suite.run(view)`.
- `asyncio.Semaphore(concurrency)` bounds parallel cases. A case that raises is
  captured as a failed `CaseResult` (recorded, not fatal) so one bad case doesn't
  sink the batch.
- Sync `run` drives `arun` with the same loop-guard pattern as `BaseAgent.run`.

## EvalReport (`report.py`)
```python
class EvalReport:
    case_results: list[CaseResult]
    def summary(self) -> dict[str, float]:      # mean per metric (+ cost, count)
    def regressions(self, baseline, threshold=0.05) -> dict[str, float]:
    def to_html(self) -> str:                    # Jinja, self-contained
    def to_dict(self) -> dict:                   # programmatic
```
- `summary`: mean of each metric across cases, total cost, case count, failures.
- `regressions(baseline)`: `baseline` is another report or a `summary()` dict;
  flag metrics whose mean dropped > threshold; return `{metric: delta}`.
- `to_html`: render `templates/report.html.j2` — summary table at top, per-case
  rows (question, per-metric scores, cost), regressions highlighted. No external
  assets (inline CSS) so it opens/attaches anywhere.

## Determinism / testing (spec §8)
- Dataset: `load` dispatches on str/list/dict (+ `NoMatchingOverloadError`);
  JSONL round-trips; malformed row raises with index.
- Runner: batch aggregates; a deliberately-failing case is captured, others
  still scored; concurrency cap respected; uses a `FakeJudge` + trivial agent so
  no network.
- Report: `regressions(baseline)` flags a deliberately-worsened metric and does
  NOT flag a stable one; `to_html` produces valid, non-empty HTML containing the
  metric names; `summary` math correct.
- Site #1 added to `test_overload_sites.py`.
- A tiny `golden_dataset.jsonl` fixture (3–5 cases) for the runner/report tests.

## OOP / reuse
- Reuses `EvalSuite`/metrics (Module 5), `Agent.arun` (Module 1), `RunResult`,
  `ConfigError` (Module 3 family), `jinja2` (already a base dep).
- No new ABC; `EvalDataset.load` is the methodoverload showcase (site #1).

## Failure modes (for DESIGN_LOG)
- Concurrency cap too high → API rate-limit errors; the reliability layer
  (Module 4) can wrap the agent to absorb them. Default 8 is conservative.
- Regression threshold is a blunt instrument (mean-based); documented as a
  first-pass signal, not statistical proof.
- HTML with very large datasets could be big; per-case table is the cost.

## Gate
Batch eval over a JSONL dataset → an `EvalReport` whose `to_html()` is valid HTML
and whose `regressions(baseline)` flags a worsened score. Mock-judge tests green;
ruff + mypy clean; ≥80% coverage; DESIGN_LOG + Context-block HARD_QUESTIONS +
module_notes/module6.md.
```
