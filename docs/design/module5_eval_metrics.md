# Module 5 — Eval Metrics (RAG): Design

> Per-module design doc, written before implementation, shaped by the
> start-of-module answers. Approve before code.

## Goal (spec §6.5, gate §5)
`EvalSuite([...]).run(result)` returns scores. Introduce the `Metric` ABC, the
four LLM-judge RAG metrics, and the polymorphic `EvalSuite`. First real consumer
of the `Judge` protocol; **inheritance + polymorphism** pillars land here, plus
**methodoverload site #4**.

## Decisions locked (start-of-module answers)
1. **Metric inputs come from `RunResult.metadata` by convention** — keys
   `question`, `contexts` (list[str]); the answer is `RunResult.output`. The
   overload also accepts a plain `dict` with the same keys for unit tests.
2. **Judge returns text; metric parses JSON with a tolerant fallback** — prompt
   for structured JSON, parse it, and on malformed output fall back to a lenient
   extraction + WARNING rather than crashing the batch.
3. **Tests inject a `FakeJudge`** returning canned JSON — deterministic, no
   network, high/low-score paths asserted per metric.
4. **`EvalSuite.run(result) -> dict[str, float]`**, then the caller applies
   `result.with_scores(dict)` once (immutability; collect-then-apply-once).
5. **Overload normalizes both inputs to an internal `MetricInput`**, then calls
   one private `_score` — the overload only does extraction, no duplicated
   scoring.
6. **Judge injected into the metric at construction** — `Faithfulness(judge=...)`.
   No global/config coupling; each metric holds its judge.
7. **Missing judge → clear error at `compute()`** naming the metric and how to
   supply one. Fail-fast.

## Files
```
agentargus/eval/
├── __init__.py
├── metrics/
│   ├── __init__.py
│   ├── base.py        # Metric (ABC), MetricInput, LLMJudgeMetric base, compute overload
│   └── rag.py         # Faithfulness, AnswerRelevance, ContextPrecision, ContextRecall
└── suite.py           # EvalSuite
tests/unit/test_metrics.py
tests/unit/test_eval_suite.py
tests/unit/test_overload_sites.py   # + site #4
```

## The abstraction (pillars: abstraction, inheritance, polymorphism)
```python
class Metric(ABC):
    name: str
    @abstractmethod
    def compute(self, source): ...        # RunResult | dict  (overload site #4)

@dataclass
class MetricInput:
    question: str
    answer: str
    contexts: tuple[str, ...]
```
- `compute` is **methodoverload site #4**: `@overload` on `RunResult`, `@overload`
  on `dict`. Both call `_to_input(...)` → `MetricInput`, then `_score(input)`.
  (`base.py` omits `from __future__ import annotations` per the Module 1 finding;
  the dict overload uses bare `dict`.)
- `LLMJudgeMetric(Metric)` — base for the four RAG metrics. Holds `self._judge`,
  raises a clear error in `_score` if it's `None`, and provides
  `_ask_json(prompt) -> dict` (calls the judge, parses JSON, tolerant fallback).

## The four RAG metrics (`rag.py`) — methodology modeled on RAGAS

> **Reuse decision:** we do NOT depend on `ragas` (it pulls LangChain + a heavy
> tree, against spec §1/§9's single-package/minimal-dep bet). Instead we model
> our metrics on RAGAS's **published methodology** (Apache-2.0), verified from
> their docs, and **credit RAGAS** in the module docstring + DESIGN_LOG. This
> gives battle-tested metric definitions without the dependency or the coupling.

Verified RAGAS formulas (from docs.ragas.io, confirmed 2026-07-19):

- **`Faithfulness`** = (claims supported by context) / (total claims). Judge
  decomposes the answer into claims and marks each inferable-from-context.
  Returns `{"claims":[...], "supported":[bool,...]}`; score 1.0 if no claims.
- **`AnswerRelevance`** — RAGAS generates N questions from the answer and takes
  the **mean cosine similarity** of their embeddings to the original question:
  `(1/N) Σ cos(E_gen_i, E_orig)`. **Needs an embedder.** We define an optional
  `Embedder` protocol (injected, like `Judge`): if present, do RAGAS's exact
  method; if absent, fall back to a judge-scored 0–1 relevance (documented as an
  approximation). Default N = 3 generated questions.
- **`ContextPrecision`** — **rank-aware Average Precision** over the retrieved
  contexts (their list order = retrieval rank). Judge marks each chunk
  relevant/not; `AP@K = Σ_k (Precision@k · rel_k) / (total relevant in top K)`.
  Rewards relevant chunks ranked higher.
- **`ContextRecall`** — decompose the **ground-truth reference** into claims;
  score = (reference claims attributable to context) / (total reference claims).
  Reads the reference from `metadata["reference"]` (populated from the eval
  dataset in Module 6); if no reference is present the metric reports
  **not-applicable** (excluded from the scores dict) rather than guessing.

Each metric's prompt + parsing lives in its class; the shared judge call/parse/
fallback lives once in `LLMJudgeMetric._ask_json`.

## Embedder protocol (new, optional — for AnswerRelevance)
```python
@runtime_checkable
class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
```
No embedding backend ships in base (framework-agnostic). Cosine similarity is a
tiny pure-Python helper in the metric (no numpy dep needed for a dot product).

## EvalSuite (`suite.py`) — polymorphism pillar
```python
class EvalSuite:
    def __init__(self, metrics: list[Metric]): ...
    def run(self, source) -> dict[str, float]:
        return {m.name: m.compute(source) for m in self.metrics}
```
Iterates `list[Metric]` calling `compute` without knowing concrete types. A
convenience `score(result) -> RunResult` may wrap `result.with_scores(run(...))`.

## Judge output parsing (tolerant)
`_ask_json`:
1. `judge.complete(prompt)` → text.
2. Try `json.loads` (and a fenced-```json``` strip).
3. On failure: log WARNING, attempt a lenient regex extraction of the expected
   keys; if that fails too, return a neutral default the metric maps to a
   conservative score (documented per metric) — never crash the batch.

## Testing plan (spec §8)
- Each metric: high score with a FakeJudge returning "all supported / relevant";
  low score with "none supported". (mock the judge — deterministic.)
- Each metric accepts BOTH `RunResult` and `dict` (overload test) + the
  `NoMatchingOverloadError` path for an unsupported type.
- Missing-judge → clear error.
- Tolerant parse: malformed judge text → WARNING + conservative score, no crash.
- `EvalSuite.run` aggregates all metrics into a dict; `score()` returns a new
  RunResult with those scores (original unchanged — immutability).
- Site #4 added to `test_overload_sites.py`.

## OOP pillars
- **Abstraction:** `Metric` ABC. **Inheritance:** RAG metrics ← `LLMJudgeMetric`
  ← `Metric` (genuine is-a). **Polymorphism:** `EvalSuite` over `list[Metric]`;
  overload-based polymorphism at site #4. Second impl proving the ABC earns its
  keep: a non-LLM heuristic metric (e.g. exact-match) — no judge needed.

## Failure modes (for DESIGN_LOG)
- Judge bias/inconsistency inflates scores — mitigated by decomposition
  (claims), not a single vague number; calibration is a known open question.
- Tolerant parsing can mask a systematically-malformed judge; the WARNING is the
  signal, and a conservative default avoids silently-high scores.
- LLM latency × 4 metrics × N dataset rows is slow — `batch_complete` (Module 0)
  is the concurrency seam, exercised in Module 6's runner.

## Gate
`EvalSuite([Faithfulness(judge), ...]).run(result)` returns scores; metrics work
on RunResult and dict; mock-judge tests green; ruff + mypy clean; ≥80% coverage;
DESIGN_LOG + Context-block HARD_QUESTIONS + module_notes/module5.md.
```
