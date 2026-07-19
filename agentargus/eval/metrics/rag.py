"""RAG evaluation metrics (spec §6.5).

Methodology modeled on **RAGAS** (https://github.com/explodinggradients/ragas,
Apache-2.0), verified from their docs. AgentArgus implements the same metric
definitions from scratch — it does NOT depend on ``ragas`` (which pulls a heavy
LangChain tree, against the single-package / minimal-dep bet in spec §1/§9).
Credit to the RAGAS authors for the metric formulations.

    Faithfulness      = supported claims / total claims
    AnswerRelevance   = mean cosine(gen_question_i, question)   [needs Embedder]
    ContextPrecision  = rank-aware Average Precision over chunks
    ContextRecall     = reference claims attributable to context / total
"""

from agentargus.config import Embedder, Judge
from agentargus.eval.metrics.base import (
    NOT_APPLICABLE,
    LLMJudgeMetric,
    MetricInput,
    cosine_similarity,
)
from agentargus.logging import get_logger

__all__ = ["Faithfulness", "AnswerRelevance", "ContextPrecision", "ContextRecall"]

_logger = get_logger("eval.metrics.rag")


class Faithfulness(LLMJudgeMetric):
    """RAGAS Faithfulness: fraction of answer claims supported by the context."""

    name = "faithfulness"

    def _score(self, inp: MetricInput) -> float:
        context = "\n".join(inp.contexts)
        prompt = (
            "Decompose the ANSWER into individual factual claims, then for each "
            "claim decide whether it can be inferred from the CONTEXT. Reply with "
            'JSON: {"claims": [..], "supported": [true/false, ..]}.\n\n'
            f"CONTEXT:\n{context}\n\nANSWER:\n{inp.answer}"
        )
        data = self._ask_json(prompt, default={"claims": [], "supported": []})
        supported = data.get("supported", [])
        if not supported:
            return 1.0  # no claims to contradict → vacuously faithful
        return sum(1 for s in supported if s) / len(supported)


class AnswerRelevance(LLMJudgeMetric):
    """RAGAS Answer Relevancy.

    With an ``Embedder``: generate N questions from the answer and take the mean
    cosine similarity of their embeddings to the original question. Without one:
    fall back to a judge-scored 0–1 relevance (documented approximation).
    """

    name = "answer_relevance"

    def __init__(
        self,
        judge: Judge | None = None,
        *,
        embedder: Embedder | None = None,
        n_questions: int = 3,
    ) -> None:
        super().__init__(judge)
        self._embedder = embedder
        self._n = n_questions

    def _score(self, inp: MetricInput) -> float:
        if self._embedder is not None:
            return self._score_with_embeddings(inp)
        return self._score_with_judge(inp)

    def _score_with_embeddings(self, inp: MetricInput) -> float:
        prompt = (
            f"Generate {self._n} distinct questions that the following ANSWER "
            'would correctly answer. Reply with JSON: {"questions": [..]}.\n\n'
            f"ANSWER:\n{inp.answer}"
        )
        data = self._ask_json(prompt, default={"questions": []})
        questions = [str(q) for q in data.get("questions", [])][: self._n]
        if not questions:
            return 0.0
        assert self._embedder is not None
        vectors = self._embedder.embed([inp.question, *questions])
        origin, gens = vectors[0], vectors[1:]
        sims = [cosine_similarity(g, origin) for g in gens]
        return sum(sims) / len(sims)

    def _score_with_judge(self, inp: MetricInput) -> float:
        prompt = (
            "Rate from 0.0 to 1.0 how well the ANSWER addresses the QUESTION "
            '(1.0 = fully relevant). Reply with JSON: {"relevance": 0.0-1.0}.\n\n'
            f"QUESTION:\n{inp.question}\n\nANSWER:\n{inp.answer}"
        )
        data = self._ask_json(prompt, default={"relevance": 0.0})
        return _clamp01(_as_float(data.get("relevance", 0.0)))


class ContextPrecision(LLMJudgeMetric):
    """RAGAS Context Precision: rank-aware Average Precision over the contexts.

    The ``contexts`` list order is treated as the retrieval ranking. The judge
    marks each chunk relevant/not; higher-ranked relevant chunks score better.
    """

    name = "context_precision"

    def _score(self, inp: MetricInput) -> float:
        if not inp.contexts:
            return 0.0
        chunks = "\n".join(f"[{i}] {c}" for i, c in enumerate(inp.contexts))
        prompt = (
            "For each numbered CONTEXT chunk, decide whether it is relevant to "
            "answering the QUESTION. Reply with JSON: "
            '{"relevant": [true/false, ..]} in the same order.\n\n'
            f"QUESTION:\n{inp.question}\n\nCONTEXTS:\n{chunks}"
        )
        data = self._ask_json(prompt, default={"relevant": []})
        flags = [bool(x) for x in data.get("relevant", [])]
        # Pad/trim to the number of contexts.
        flags = (flags + [False] * len(inp.contexts))[: len(inp.contexts)]
        total_relevant = sum(flags)
        if total_relevant == 0:
            return 0.0
        # Average Precision: sum(Precision@k * rel_k) / total_relevant.
        hits = 0
        ap = 0.0
        for k, rel in enumerate(flags, start=1):
            if rel:
                hits += 1
                ap += hits / k
        return ap / total_relevant


class ContextRecall(LLMJudgeMetric):
    """RAGAS Context Recall: fraction of GROUND-TRUTH reference claims that are
    attributable to the retrieved context.

    Requires a ground-truth ``reference`` (from the eval dataset). If none is
    present, returns NOT_APPLICABLE so EvalSuite excludes it rather than guessing.
    """

    name = "context_recall"

    def _score(self, inp: MetricInput) -> float:
        if not inp.reference:
            _logger.info("context_recall: no ground-truth reference; not applicable.")
            return NOT_APPLICABLE
        context = "\n".join(inp.contexts)
        prompt = (
            "Decompose the REFERENCE answer into individual claims, then for each "
            "decide whether it can be attributed to (supported by) the CONTEXT. "
            'Reply with JSON: {"claims": [..], "supported": [true/false, ..]}.\n\n'
            f"CONTEXT:\n{context}\n\nREFERENCE:\n{inp.reference}"
        )
        data = self._ask_json(prompt, default={"claims": [], "supported": []})
        supported = data.get("supported", [])
        if not supported:
            return NOT_APPLICABLE
        return sum(1 for s in supported if s) / len(supported)


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))
