"""RAG-over-resume agent for the AgentArgus example.

Loads a resume (PDF via pypdf, or a .txt fallback), splits it into chunks, does a
tiny keyword retrieval to pick the most relevant chunks for a question, and asks
the LLM to answer grounded ONLY in those chunks. The retrieved chunks are
recorded so AgentArgus's RAG metrics (Faithfulness / AnswerRelevance /
ContextPrecision) can score the answer.

AgentArgus evaluates RAG; it does not do retrieval for you — so the retriever
here is deliberately minimal (no vector DB), just enough to exercise the metrics.
"""

from __future__ import annotations

import re
from pathlib import Path

_HERE = Path(__file__).parent


def load_resume() -> str:
    """Return resume text.

    Resolution order: your ``resume.pdf`` → your ``resume.txt`` (both gitignored,
    so your real resume is never committed) → the shipped ``resume.sample.txt``
    (a fictional sample so the example runs out-of-the-box for anyone).
    """
    pdf = _HERE / "resume.pdf"
    if pdf.exists():
        from pypdf import PdfReader

        reader = PdfReader(str(pdf))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    for name in ("resume.txt", "resume.sample.txt"):
        f = _HERE / name
        if f.exists():
            return f.read_text(encoding="utf-8")
    raise FileNotFoundError(
        "No resume found. Add examples/resume_rag/resume.pdf or resume.txt "
        "(the shipped resume.sample.txt is used otherwise)."
    )


def chunk(text: str, *, max_chars: int = 400) -> list[str]:
    """Split into paragraph-ish chunks (blank-line separated, then size-bounded)."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    for para in paragraphs:
        if len(para) <= max_chars:
            chunks.append(para)
        else:
            for i in range(0, len(para), max_chars):
                chunks.append(para[i : i + max_chars])
    return chunks


def retrieve(question: str, chunks: list[str], *, k: int = 3) -> list[str]:
    """Naive keyword-overlap retrieval — top-k chunks by shared word count."""
    q_words = {w for w in re.findall(r"\w+", question.lower()) if len(w) > 2}

    def score(c: str) -> int:
        c_words = set(re.findall(r"\w+", c.lower()))
        return len(q_words & c_words)

    ranked = sorted(chunks, key=score, reverse=True)
    return [c for c in ranked[:k] if score(c) > 0] or chunks[:k]


def make_resume_agent(llm: object):
    """Build a callable agent(question) -> answer, using ``llm`` (a Judge).

    Records the retrieved contexts + the LLM tool call so AgentArgus can trace,
    cost, and evaluate the run.
    """
    from agentargus import record_step, record_tool_call

    resume_text = load_resume()
    chunks = chunk(resume_text)

    def agent(question: str) -> str:
        record_step("reason", f"retrieving resume chunks for: {question}")
        contexts = retrieve(question, chunks)
        # Expose the contexts so metrics can read them (RunResult.metadata).
        record_tool_call("retrieve", {"question": question}, contexts, success=True)
        prompt = (
            "Answer the QUESTION about the candidate using ONLY the CONTEXT from "
            "their resume. If the context doesn't contain the answer, say so.\n\n"
            f"CONTEXT:\n{chr(10).join(contexts)}\n\nQUESTION: {question}"
        )
        answer = llm.complete(prompt)  # type: ignore[attr-defined]
        record_step("answer", answer)
        # Stash contexts for the eval metrics via the recorder-independent path:
        agent.last_contexts = contexts  # type: ignore[attr-defined]
        return answer

    agent.__name__ = "resume_rag"
    return agent, chunks
