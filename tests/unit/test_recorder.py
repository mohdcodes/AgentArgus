"""Tests for the run Recorder (spec §8: recording + contextvar isolation)."""

from __future__ import annotations

from agentargus.agents.recorder import (
    Recorder,
    current_recorder,
    record_tool_call,
    reset_recorder,
    set_recorder,
)


class TestRecorder:
    def test_records_tool_calls_and_steps(self) -> None:
        rec = Recorder()
        rec.record_tool_call("search", {"q": "x"}, ["r"], success=True)
        rec.record_step("reason", "thinking")
        assert rec.tool_calls[0].name == "search"
        assert rec.tool_calls[0].success is True
        assert rec.steps[0].kind == "reason"
        assert rec.steps[0].index == 0


class TestContextvar:
    def test_no_recorder_is_noop(self) -> None:
        # No active recorder → record_tool_call is a silent no-op (no crash).
        record_tool_call("orphan")

    def test_module_level_writes_to_active_recorder(self) -> None:
        rec = Recorder()
        token = set_recorder(rec)
        try:
            record_tool_call("search", success=True)
            assert current_recorder() is rec
        finally:
            reset_recorder(token)
        assert rec.tool_calls[0].name == "search"
        assert current_recorder() is None  # cleared after reset

    def test_explicit_recorder_bypasses_contextvar(self) -> None:
        # For the worker-thread case: pass recorder= explicitly.
        rec = Recorder()
        record_tool_call("threaded", recorder=rec)
        assert rec.tool_calls[0].name == "threaded"
