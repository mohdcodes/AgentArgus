"""Workers for the deep-research demo.

Three workers form a handoff chain: retrieval -> analysis -> synthesis. The
retrieval worker (a) gates an "expensive crawl" behind a HITL checkpoint and
(b) uses a flaky search tool that fails twice then succeeds — so the demo shows
reliability recovering an injected failure. Each worker records its tool calls
and reasoning steps for the agent-behaviour metrics.
"""

from __future__ import annotations

from typing import Any

from agentargus import (
    Agent,
    AutoApproveBackend,
    Checkpoint,
    Handoff,
    ReliabilityPolicy,
    RetryWithBackoff,
    TransientError,
    record_step,
    record_tool_call,
)

# A tiny "knowledge base" the mock retrieval searches (keyword match).
_KB = {
    "quantum": "Quantum computing uses qubits; 2024 saw error-correction milestones.",
    "tesla": "Tesla 2023 revenue was $96.8B, up ~19% from $81.5B in 2022.",
    "python": "Python 3.13 introduced an experimental free-threaded build.",
    "llm": "LLM agents combine planning, tool use, and memory for autonomy.",
}


def _make_flaky_search() -> Any:
    """A search tool that raises TransientError on its first 2 calls per query."""
    attempts: dict[str, int] = {}

    def search(query: str) -> list[str]:
        attempts[query] = attempts.get(query, 0) + 1
        if attempts[query] <= 2:
            raise TransientError(f"search backend 503 (attempt {attempts[query]})")
        hits = [v for k, v in _KB.items() if k in query.lower()]
        return hits or ["No specific source found; general knowledge only."]

    return search


_KB_LIST = list(_KB.values())


def make_rag_agent(llm: object):
    """A SIMPLE single agent for the eval batch: retrieve -> answer, exposing
    the retrieved contexts on RunResult.metadata so RAG metrics can score them.

    (The supervisor below demonstrates multi-agent orchestration separately; a
    supervisor hides contexts inside handoffs, which is why the eval batch uses
    this straightforward agent instead.)
    """
    from agentargus import record_step, record_tool_call

    def agent(question: str) -> str:
        record_step("reason", f"retrieving for: {question}")
        contexts = [v for k, v in _KB.items() if k in question.lower()] or _KB_LIST[:2]
        record_tool_call("web_search", {"q": question}, contexts, success=True)
        record_tool_call("analyze", {"q": question}, "key points", success=True)
        prompt = (
            "Answer the QUESTION using ONLY this CONTEXT.\n\n"
            f"CONTEXT:\n{chr(10).join(contexts)}\n\nQUESTION: {question}"
        )
        answer = llm.complete(prompt)  # type: ignore[attr-defined]
        agent.last_contexts = contexts  # type: ignore[attr-defined]
        return answer

    agent.__name__ = "rag_agent"
    return agent


def make_workers(llm: object) -> dict[str, Agent]:
    """Build the three worker agents. ``llm`` is a Judge (real or mock)."""
    flaky_search = _make_flaky_search()

    # ---- retrieval worker: HITL gate + flaky tool wrapped in retry ---------- #
    async def retrieval(question: str) -> Handoff:
        # HITL: approve the (pretend) expensive crawl before doing it (M9).
        gate = Checkpoint(AutoApproveBackend(), name="expensive_crawl")
        await gate.require_approval({"action": "web crawl", "question": question})

        record_step("reason", f"searching sources for: {question}")
        contexts = flaky_search(question)  # runs under the worker's reliability
        record_tool_call("web_search", {"q": question}, contexts, success=True)
        return Handoff(target="analysis", input=question, context={"contexts": contexts})

    # Wrap retrieval in reliability so the flaky tool's failures are recovered.
    retrieval_agent = Agent(
        retrieval,
        reliability=ReliabilityPolicy(retry=RetryWithBackoff(max_attempts=4)),
        name="retrieval",
    )

    # ---- analysis worker ---------------------------------------------------- #
    async def analysis(question: str) -> Handoff:
        record_step("reason", "analyzing retrieved sources")
        record_tool_call("analyze", {"q": question}, "key points extracted", success=True)
        return Handoff(target="synthesis", input=question)

    # ---- synthesis worker: composes the final answer (uses the LLM) --------- #
    async def synthesis(question: str) -> str:
        record_step("reason", "composing final answer")
        prompt = f"Answer this research question concisely: {question}"
        return llm.complete(prompt)  # type: ignore[attr-defined]

    return {
        "retrieval": retrieval_agent,
        "analysis": Agent(analysis, name="analysis"),
        "synthesis": Agent(synthesis, name="synthesis"),
    }


class StaticRouter:
    """Always routes the initial question to retrieval (chain does the rest)."""

    def route(self, input: Any, workers: dict[str, Agent]) -> str:
        return "retrieval"
