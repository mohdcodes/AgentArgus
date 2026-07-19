"""Tests for the checkpointer (spec §8: round-trip, SQLite restart, resume)."""

from __future__ import annotations

from pathlib import Path

from agentargus.agents.checkpoint_store import (
    STATUS_COMPLETED,
    STATUS_RUNNING,
    InMemoryCheckpointer,
    SqliteCheckpointer,
)


class TestInMemory:
    def test_round_trip(self) -> None:
        cp = InMemoryCheckpointer()
        cp.save_step("r1", 0, "a", {"in": 1}, {"out": 2}, STATUS_COMPLETED)
        steps = cp.load_steps("r1")
        assert steps[0]["worker"] == "a"
        assert steps[0]["output"] == {"out": 2}

    def test_run_id_scoping(self) -> None:
        cp = InMemoryCheckpointer()
        cp.save_step("r1", 0, "a", 1, 2, STATUS_COMPLETED)
        cp.save_step("r2", 0, "b", 3, 4, STATUS_COMPLETED)
        assert len(cp.load_steps("r1")) == 1
        assert cp.load_steps("r1")[0]["worker"] == "a"

    def test_running_updated_to_completed_in_place(self) -> None:
        cp = InMemoryCheckpointer()
        cp.save_step("r1", 0, "a", 1, None, STATUS_RUNNING)
        cp.save_step("r1", 0, "a", 1, "done", STATUS_COMPLETED)
        steps = cp.load_steps("r1")
        assert len(steps) == 1  # replaced, not duplicated
        assert steps[0]["status"] == STATUS_COMPLETED

    def test_last_completed_step(self) -> None:
        cp = InMemoryCheckpointer()
        cp.save_step("r1", 0, "a", 1, 1, STATUS_COMPLETED)
        cp.save_step("r1", 1, "b", 2, 2, STATUS_COMPLETED)
        cp.save_step("r1", 2, "c", 3, None, STATUS_RUNNING)
        assert cp.last_completed_step("r1") == 1


class TestSqlite:
    def test_wal_mode_enabled(self, tmp_path: Path) -> None:
        cp = SqliteCheckpointer(tmp_path / "cp.db")
        mode = cp._conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert mode.lower() == "wal"
        cp.close()

    def test_persists_across_fresh_instance(self, tmp_path: Path) -> None:
        # Simulates a process restart: write, drop the object, reopen the file.
        path = tmp_path / "cp.db"
        cp1 = SqliteCheckpointer(path)
        cp1.save_step("r1", 0, "retrieval", {"q": "x"}, {"docs": 3}, STATUS_COMPLETED)
        cp1.close()

        cp2 = SqliteCheckpointer(path)  # "restart"
        steps = cp2.load_steps("r1")
        assert steps[0]["worker"] == "retrieval"
        assert steps[0]["output"] == {"docs": 3}
        assert cp2.last_completed_step("r1") == 0
        cp2.close()

    def test_completed_output_replay(self, tmp_path: Path) -> None:
        cp = SqliteCheckpointer(tmp_path / "cp.db")
        cp.save_step("r1", 0, "a", "in", "cached-out", STATUS_COMPLETED)
        assert cp.completed_output("r1", 0) == "cached-out"
        cp.close()

    def test_memory_db(self) -> None:
        cp = SqliteCheckpointer(":memory:")
        cp.save_step("r1", 0, "a", 1, 2, STATUS_COMPLETED)
        assert cp.load_steps("r1")[0]["output"] == 2
        cp.close()
