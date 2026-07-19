"""Core domain objects for AgentArgus.

This module defines ``RunResult`` — the single canonical data object that is the
spine of the whole system (see spec §2) — and its supporting value objects.

Design posture (decided in the Module 0 discussion, recorded in DESIGN_LOG):

*   Every object here is a **deeply immutable** frozen dataclass. Collection
    fields are stored as ``tuple`` (never ``list``) so a caller cannot mutate
    ``result.spans`` in place. This directly answers the HARD_QUESTION
    "``spans`` is a list — is it actually immutable?" with a provable *yes*.
*   ``RunResult.scores`` is populated *after* evaluation. Rather than mutate the
    object in place, ``with_scores`` returns a **new** ``RunResult`` — resolving
    checkpoint 2.1 in favour of immutability.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

from agentargus._internal.exceptions import SerializationError

__all__ = [
    "Span",
    "ToolCall",
    "Step",
    "ErrorRecord",
    "CostBreakdown",
    "RunResult",
]


def _freeze_mapping(m: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a read-only view of a mapping so dict fields cannot be mutated."""
    return MappingProxyType(dict(m))


def _jsonable(value: Any, *, field_name: str) -> Any:
    """Coerce an arbitrary value into a JSON-serializable form, or fail loudly.

    Strategy (HARD_QUESTIONS #7): try plain JSON first; if that fails, honour a
    ``.to_dict()`` method if the object defines one; otherwise raise a
    ``SerializationError`` naming the exact field and type. A silent lossy
    fallback (e.g. ``str(value)``) is deliberately rejected — it would hide real
    data loss from the caller.
    """
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        pass
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        candidate = to_dict()
        try:
            json.dumps(candidate)
            return candidate
        except (TypeError, ValueError):
            pass
    raise SerializationError(
        f"Field {field_name!r} holds a value of type "
        f"{type(value).__name__!r} that is not JSON-serializable and has no "
        f"usable .to_dict(). Convert it before building the RunResult."
    )


@dataclass(frozen=True, slots=True)
class Span:
    """A single structured execution step, emitted by the tracer (spec §2).

    ``start_time`` / ``end_time`` are float **seconds** for display and the
    public API. But float64 cannot hold epoch-**nanosecond** precision (~19
    significant digits vs float's ~16), so two spans less than ~1µs apart would
    collapse to the same float — and anything ordering by start time (timeline
    reconstruction, sibling ordering) would see a tie. ``start_ns`` / ``end_ns``
    keep the lossless integer nanoseconds when the source provides them; sort by
    ``sort_key`` (ns when available, else float seconds) to preserve true order.
    (HARD_QUESTIONS Module 2 #5.)
    """

    name: str
    span_id: str
    start_time: float
    end_time: float
    attributes: Mapping[str, Any] = field(default_factory=dict)
    parent_id: str | None = None
    start_ns: int | None = None
    end_ns: int | None = None

    def __post_init__(self) -> None:
        # Freeze the mapping so ``span.attributes["x"] = ...`` raises.
        object.__setattr__(self, "attributes", _freeze_mapping(self.attributes))

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    @property
    def sort_key(self) -> int | float:
        """Lossless ordering key: nanoseconds if known, else float seconds."""
        return self.start_ns if self.start_ns is not None else self.start_time


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A single tool invocation: name, args, result, success, latency (spec §2)."""

    name: str
    args: Mapping[str, Any]
    result: Any
    success: bool
    latency: float
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "args", _freeze_mapping(self.args))


@dataclass(frozen=True, slots=True)
class Step:
    """An ordered reasoning/action step, used by plan-coherence eval (spec §2)."""

    index: int
    kind: str  # e.g. "reason" | "action" | "observation"
    content: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class ErrorRecord:
    """Anything caught by the reliability layer (spec §2)."""

    error_type: str
    message: str
    recovered: bool
    attempt: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    """Token + dollar accounting for a run (produced by CostTracker, spec §2)."""

    input_tokens: int = 0
    output_tokens: int = 0
    input_cost: float = 0.0
    output_cost: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def total_cost(self) -> float:
        return self.input_cost + self.output_cost

    def __add__(self, other: CostBreakdown) -> CostBreakdown:
        if not isinstance(other, CostBreakdown):
            return NotImplemented
        return CostBreakdown(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            input_cost=self.input_cost + other.input_cost,
            output_cost=self.output_cost + other.output_cost,
        )


@dataclass(frozen=True, slots=True)
class RunResult:
    """The canonical result of one wrapped agent run — the spine of AgentArgus.

    Collection fields are stored as tuples for genuine immutability. Use the
    typed sequence accessors; the underlying storage is never a mutable list.
    ``scores`` starts empty and is filled by ``with_scores`` after eval, which
    returns a *new* object rather than mutating this one.
    """

    output: Any
    trace_id: str
    spans: Sequence[Span] = ()
    cost: CostBreakdown = field(default_factory=CostBreakdown)
    tool_calls: Sequence[ToolCall] = ()
    steps: Sequence[Step] = ()
    errors: Sequence[ErrorRecord] = ()
    scores: Mapping[str, float] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Coerce every collection to an immutable form, regardless of what the
        # caller passed (list, generator, tuple). One home for the coercion.
        object.__setattr__(self, "spans", tuple(self.spans))
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))
        object.__setattr__(self, "steps", tuple(self.steps))
        object.__setattr__(self, "errors", tuple(self.errors))
        object.__setattr__(self, "scores", _freeze_mapping(self.scores))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    def with_scores(self, scores: Mapping[str, float]) -> RunResult:
        """Return a NEW RunResult with ``scores`` merged in (immutability)."""
        merged = {**self.scores, **scores}
        return replace(self, scores=merged)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (JSON-friendly). Round-trips via from_dict."""
        return {
            "output": _jsonable(self.output, field_name="output"),
            "trace_id": self.trace_id,
            "spans": [
                {
                    "name": s.name,
                    "span_id": s.span_id,
                    "start_time": s.start_time,
                    "end_time": s.end_time,
                    "attributes": dict(s.attributes),
                    "parent_id": s.parent_id,
                    "start_ns": s.start_ns,
                    "end_ns": s.end_ns,
                }
                for s in self.spans
            ],
            "cost": {
                "input_tokens": self.cost.input_tokens,
                "output_tokens": self.cost.output_tokens,
                "input_cost": self.cost.input_cost,
                "output_cost": self.cost.output_cost,
            },
            "tool_calls": [
                {
                    "name": t.name,
                    "args": dict(t.args),
                    "result": _jsonable(t.result, field_name=f"tool_calls[{i}].result"),
                    "success": t.success,
                    "latency": t.latency,
                    "error": t.error,
                }
                for i, t in enumerate(self.tool_calls)
            ],
            "steps": [
                {
                    "index": st.index,
                    "kind": st.kind,
                    "content": st.content,
                    "metadata": dict(st.metadata),
                }
                for st in self.steps
            ],
            "errors": [
                {
                    "error_type": e.error_type,
                    "message": e.message,
                    "recovered": e.recovered,
                    "attempt": e.attempt,
                    "metadata": dict(e.metadata),
                }
                for e in self.errors
            ],
            "scores": dict(self.scores),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RunResult:
        """Reconstruct a RunResult from ``to_dict`` output."""
        cost_data = data.get("cost", {})
        return cls(
            output=data["output"],
            trace_id=data["trace_id"],
            spans=tuple(Span(**s) for s in data.get("spans", [])),
            cost=CostBreakdown(**cost_data),
            tool_calls=tuple(ToolCall(**t) for t in data.get("tool_calls", [])),
            steps=tuple(Step(**st) for st in data.get("steps", [])),
            errors=tuple(ErrorRecord(**e) for e in data.get("errors", [])),
            scores=dict(data.get("scores", {})),
            metadata=dict(data.get("metadata", {})),
        )
