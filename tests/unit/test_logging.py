"""Tests for the logging system (spec §7: color gating, JSON, trace correlation)."""

from __future__ import annotations

import io
import json
import logging

import pytest
from agentargus.logging import (
    ColorFormatter,
    JsonFormatter,
    _should_use_color,
    configure_logging,
    get_logger,
    get_trace_id,
    set_trace_id,
)


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class TestColorGating:
    def test_no_color_when_not_tty(self) -> None:
        assert _should_use_color(True, io.StringIO()) is False

    def test_color_when_tty(self) -> None:
        assert _should_use_color(True, _FakeTTY()) is True

    def test_no_color_env_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NO_COLOR", "1")
        assert _should_use_color(True, _FakeTTY()) is False

    def test_flag_off_disables(self) -> None:
        assert _should_use_color(False, _FakeTTY()) is False


class TestFormatters:
    def _record(self, msg: str = "hello", level: int = logging.INFO) -> logging.LogRecord:
        rec = logging.LogRecord("agentargus.test", level, __file__, 1, msg, None, None)
        rec.trace_id = "trace-1234567890"
        return rec

    def test_color_formatter_includes_ansi_when_enabled(self) -> None:
        out = ColorFormatter(use_color=True).format(self._record())
        assert "\033[" in out
        assert "trace-12" in out  # first 8 chars of trace_id

    def test_color_formatter_plain_when_disabled(self) -> None:
        out = ColorFormatter(use_color=False).format(self._record())
        assert "\033[" not in out

    def test_json_formatter_is_valid_json(self) -> None:
        out = JsonFormatter().format(self._record())
        parsed = json.loads(out)
        assert parsed["level"] == "INFO"
        assert parsed["trace_id"] == "trace-1234567890"
        assert parsed["message"] == "hello"


class TestTraceCorrelation:
    def test_set_and_get(self) -> None:
        token = set_trace_id("abc")
        try:
            assert get_trace_id() == "abc"
        finally:
            import agentargus.logging as lg

            lg._trace_id_var.reset(token)

    def test_trace_id_appears_in_output(self) -> None:
        stream = io.StringIO()
        configure_logging(level="DEBUG", color=False, json_format=True, stream=stream)
        token = set_trace_id("corr-999")
        try:
            get_logger("x").info("with trace")
        finally:
            import agentargus.logging as lg

            lg._trace_id_var.reset(token)
        line = stream.getvalue().strip().splitlines()[-1]
        assert json.loads(line)["trace_id"] == "corr-999"


class TestFactory:
    def test_namespaced(self) -> None:
        assert get_logger("agents").name == "agentargus.agents"

    def test_root(self) -> None:
        assert get_logger().name == "agentargus"

    def test_levels_emit(self) -> None:
        stream = io.StringIO()
        configure_logging(level="WARNING", color=False, stream=stream)
        log = get_logger("lvl")
        log.info("should not appear")
        log.warning("should appear")
        output = stream.getvalue()
        assert "should not appear" not in output
        assert "should appear" in output
