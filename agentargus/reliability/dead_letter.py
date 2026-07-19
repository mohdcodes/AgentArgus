"""Dead-letter queue (spec §6.4).

Permanently-failed inputs (those that survived retry + fallback + breaker) are
persisted for later inspection/replay. ``DeadLetterSink`` is the abstraction
(pillar) so the JSONL default can be swapped for Redis/DB/SQS without touching
the queue. The JSONL sink is append-only.
"""

from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from agentargus.logging import get_logger

__all__ = ["DeadLetterSink", "JsonlDeadLetterSink", "InMemoryDeadLetterSink", "DeadLetterQueue"]

_logger = get_logger("reliability.dead_letter")


class DeadLetterSink(ABC):
    """Where dead-lettered records go. Swap the backend by subclassing."""

    @abstractmethod
    def append(self, record: dict[str, Any]) -> None:
        """Persist one dead-letter record."""
        raise NotImplementedError


class JsonlDeadLetterSink(DeadLetterSink):
    """Append-only JSONL file sink (default). One JSON object per line."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    def append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, default=str)
        with self._lock, self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


class InMemoryDeadLetterSink(DeadLetterSink):
    """In-memory sink — useful for tests. Not durable across a crash."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def append(self, record: dict[str, Any]) -> None:
        self.records.append(record)


class DeadLetterQueue:
    """Wraps a sink; the policy hands it inputs that failed everything else."""

    def __init__(self, sink: DeadLetterSink) -> None:
        self._sink = sink

    def put(self, *, input: Any, error: BaseException, step: str = "call") -> None:
        """Record a permanently-failed input plus the terminal error."""
        self._sink.append(
            {
                "step": step,
                "input": input,
                "error_type": type(error).__name__,
                "error": str(error),
            }
        )
        _logger.error(
            "dead-lettered input at step=%s after terminal %s",
            step,
            type(error).__name__,
        )
