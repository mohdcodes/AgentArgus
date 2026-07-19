# AgentArgus examples

Two runnable agents wrapped with AgentArgus, so you can see traces, cost, and
eval scores on real work.

## Setup

```bash
uv pip install -e ".[dev,examples]"     # installs anthropic + pypdf for examples
cp .env.example .env                    # then put your real key in .env (gitignored)
# .env:  ANTHROPIC_API_KEY=sk-ant-...
```

Without a key the examples run with a **MockJudge** (synthetic answers/scores) so
the wiring works anywhere; with a key you get real answers and real scores.

## 1. Resume RAG (`resume_rag/`)

Answers questions about a candidate, grounded in their resume, and scores the
answers with RAG metrics (Faithfulness / AnswerRelevance / ContextPrecision).

```bash
# put your resume at examples/resume_rag/resume.pdf  (a resume.txt fallback exists)
uv run python examples/resume_rag/run.py
```
Shows per-question: the answer, `trace_id`, cost, and the RAG scores.

## 2. Multi-tool agent (`tool_agent/`)

An agent with a calculator, a (mock) web search, and a clock. It plans which
tools to use, calls them, and is scored on tool behaviour (ToolUseAccuracy vs. an
expected-tools label, ToolSuccessRate).

```bash
uv run python examples/tool_agent/run.py
```
Shows per-case: the answer, tools actually called vs. expected, and the scores.

## Seeing traces in Jaeger (optional)

```bash
docker run -d --name jaeger -p 16686:16686 -p 4318:4318 jaegertracing/all-in-one
```
Then use `Tracer(exporter="otlp")` in the run script and open
<http://localhost:16686>, searching by the `trace_id` the run prints. (The
examples default to the in-memory tracer; spans are on `result.spans` regardless.)

## Note on keys
`.env` is gitignored; **never** put a real key in `.env.example` (it's committed).
