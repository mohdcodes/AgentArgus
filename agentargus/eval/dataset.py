"""Eval dataset (spec §6.5) — methodoverload site #1.

``EvalDataset.load(source)`` dispatches on the source *type*: a ``str`` path
(``.jsonl``/``.json``), an in-memory ``list`` of records, or a single ``dict``
record. Each record becomes an ``EvalCase``.
"""

# NOTE: NO ``from __future__ import annotations`` — methodoverload dispatches on
# runtime annotations via isinstance; PEP 563 stringization breaks that. Overloads
# use bare ``str``/``list``/``dict``. See docs/concepts/methodoverload.md.

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from methodoverload import overload

from agentargus._internal.exceptions import ConfigError
from agentargus.logging import get_logger

__all__ = ["EvalCase", "EvalDataset"]

_logger = get_logger("eval.dataset")


@dataclass(frozen=True, slots=True)
class EvalCase:
    """One evaluation case. ``question`` is required; the rest are optional."""

    question: str
    reference: str | None = None
    contexts: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "contexts", tuple(self.contexts))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @classmethod
    def from_record(cls, record: Mapping[str, Any], *, index: int = 0) -> "EvalCase":
        question = record.get("question")
        if not question or not str(question).strip():
            raise ConfigError(f"Eval case at index {index} is missing a non-empty 'question'.")
        return cls(
            question=str(question),
            reference=record.get("reference"),
            contexts=tuple(record.get("contexts", ()) or ()),
            metadata=dict(record.get("metadata", {}) or {}),
        )


class EvalDataset:
    """An ordered collection of ``EvalCase``s."""

    def __init__(self, cases: list[EvalCase] | None = None) -> None:
        self.cases: tuple[EvalCase, ...] = tuple(cases or ())

    def __len__(self) -> int:
        return len(self.cases)

    def __iter__(self) -> Any:
        return iter(self.cases)

    # ------------------------------------------------------------------ #
    # load() — methodoverload site #1 (dispatch on source type)
    # ------------------------------------------------------------------ #
    @overload
    def load(self, source: str) -> "EvalDataset":
        """Load from a file path (``.jsonl`` line-delimited, or ``.json`` array)."""
        path = Path(source)
        if path.suffix == ".jsonl":
            return self._from_records(_read_jsonl(path))
        if path.suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            records = data if isinstance(data, list) else [data]
            return self._from_records(records)
        raise ConfigError(f"Unsupported dataset file {source!r}; expected .jsonl or .json.")

    @overload  # type: ignore[no-redef]  # methodoverload merges runtime overloads
    def load(self, source: list) -> "EvalDataset":  # type: ignore[type-arg]  # bare list REQUIRED for isinstance dispatch  # noqa: F811
        """Load from an in-memory list of record dicts."""
        return self._from_records(source)

    @overload  # type: ignore[no-redef]
    def load(self, source: dict) -> "EvalDataset":  # type: ignore[type-arg]  # bare dict REQUIRED for isinstance dispatch  # noqa: F811
        """Wrap a single record dict as a one-case dataset."""
        return self._from_records([source])

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "EvalDataset":
        """Convenience constructor: load a ``.jsonl`` file."""
        return cls()._from_records(_read_jsonl(Path(path)))

    @staticmethod
    def _from_records(records: list[Mapping[str, Any]]) -> "EvalDataset":
        cases = [EvalCase.from_record(r, index=i) for i, r in enumerate(records)]
        _logger.info("loaded eval dataset with %d cases", len(cases))
        return EvalDataset(cases)


def _read_jsonl(path: Path) -> list[Mapping[str, Any]]:
    if not path.exists():
        raise ConfigError(f"Dataset file not found: {path}")
    records: list[Mapping[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Malformed JSON on line {line_no} of {path}: {exc}") from exc
    return records
