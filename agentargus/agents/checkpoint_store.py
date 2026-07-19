"""Orchestration checkpointing (spec §6.6) — production-hardened.

Persists per-step orchestration state so a supervised run resumes across a
process restart. ``Checkpointer`` is the abstraction; ``SqliteCheckpointer`` is
the durable default and ``InMemoryCheckpointer`` is for tests.

Production hardening (all stdlib, no new deps):
* **WAL mode** — better concurrent reads and crash resilience than the default
  rollback journal.
* **status column** (``running`` / ``completed`` / ``failed``) — a step killed
  mid-write is left ``running`` and is *re-run* on resume, never trusted.
* **run_id scoping** — every query is keyed by ``run_id`` so concurrent runs
  sharing one file never see each other's steps.
* **write lock** — SQLite is single-writer; a ``threading.Lock`` serialises our
  writes cleanly under the async-core + ``to_thread`` reality.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from agentargus.logging import get_logger

__all__ = [
    "Checkpointer",
    "SqliteCheckpointer",
    "InMemoryCheckpointer",
    "STATUS_RUNNING",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
]

_logger = get_logger("agents.checkpoint")

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


class Checkpointer(ABC):
    """Persists and restores per-step orchestration state, keyed by run_id."""

    @abstractmethod
    def save_step(
        self, run_id: str, step: int, worker: str, input: Any, output: Any, status: str
    ) -> None: ...

    @abstractmethod
    def load_steps(self, run_id: str) -> list[dict[str, Any]]: ...

    def last_completed_step(self, run_id: str) -> int:
        """Highest step index with status=completed for ``run_id`` (-1 if none)."""
        completed = [s["step"] for s in self.load_steps(run_id) if s["status"] == STATUS_COMPLETED]
        return max(completed) if completed else -1

    def completed_output(self, run_id: str, step: int) -> Any:
        """Cached output of a completed step (for resume), or None."""
        for s in self.load_steps(run_id):
            if s["step"] == step and s["status"] == STATUS_COMPLETED:
                return s["output"]
        return None


class InMemoryCheckpointer(Checkpointer):
    """Non-durable checkpointer for tests."""

    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def save_step(
        self, run_id: str, step: int, worker: str, input: Any, output: Any, status: str
    ) -> None:
        with self._lock:
            # Replace any existing row for (run_id, step) so a running->completed
            # transition updates in place.
            self._rows = [
                r for r in self._rows if not (r["run_id"] == run_id and r["step"] == step)
            ]
            self._rows.append(
                {
                    "run_id": run_id,
                    "step": step,
                    "worker": worker,
                    "input": input,
                    "output": output,
                    "status": status,
                }
            )

    def load_steps(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = [dict(r) for r in self._rows if r["run_id"] == run_id]
        return sorted(rows, key=lambda r: r["step"])


class SqliteCheckpointer(Checkpointer):
    """Durable SQLite checkpointer (WAL, status flag, run_id-scoped, locked)."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        self._lock = threading.Lock()
        # check_same_thread=False + our own lock so the connection can be used
        # from asyncio.to_thread worker threads safely.
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkpoints (
                run_id     TEXT NOT NULL,
                step       INTEGER NOT NULL,
                worker     TEXT NOT NULL,
                input_json TEXT,
                output_json TEXT,
                status     TEXT NOT NULL,
                PRIMARY KEY (run_id, step)
            )
            """
        )
        self._conn.commit()

    def save_step(
        self, run_id: str, step: int, worker: str, input: Any, output: Any, status: str
    ) -> None:
        # JSON-encode; a non-serializable payload fails loudly here, not silently.
        input_json = json.dumps(input, default=str)
        output_json = json.dumps(output, default=str) if output is not None else None
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO checkpoints (run_id, step, worker, input_json, output_json, status)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, step) DO UPDATE SET
                    worker=excluded.worker,
                    input_json=excluded.input_json,
                    output_json=excluded.output_json,
                    status=excluded.status
                """,
                (run_id, step, worker, input_json, output_json, status),
            )
            self._conn.commit()  # atomic per-step commit

    def load_steps(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT step, worker, input_json, output_json, status "
                "FROM checkpoints WHERE run_id = ? ORDER BY step",
                (run_id,),
            )
            rows = cursor.fetchall()
        return [
            {
                "step": r[0],
                "worker": r[1],
                "input": json.loads(r[2]) if r[2] is not None else None,
                "output": json.loads(r[3]) if r[3] is not None else None,
                "status": r[4],
            }
            for r in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
