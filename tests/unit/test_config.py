"""Tests for AgentArgusConfig (env resolution + overrides + Judge protocol)."""

from __future__ import annotations

import pytest
from agentargus.config import AgentArgusConfig, Judge


class TestFromEnv:
    def test_defaults_when_env_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for k in list(dict(__import__("os").environ)):
            if k.startswith("AGENTARGUS_"):
                monkeypatch.delenv(k, raising=False)
        cfg = AgentArgusConfig.from_env()
        assert cfg.judge_model == "claude-opus-4-8"
        assert cfg.cost_ceiling_usd is None
        assert cfg.log_level == "INFO"
        assert cfg.log_color is True

    def test_reads_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENTARGUS_JUDGE_MODEL", "claude-sonnet-5")
        monkeypatch.setenv("AGENTARGUS_COST_CEILING_USD", "1.5")
        monkeypatch.setenv("AGENTARGUS_LOG_COLOR", "false")
        monkeypatch.setenv("AGENTARGUS_LOG_JSON", "true")
        cfg = AgentArgusConfig.from_env()
        assert cfg.judge_model == "claude-sonnet-5"
        assert cfg.cost_ceiling_usd == 1.5
        assert cfg.log_color is False
        assert cfg.log_json is True

    def test_overrides_win_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENTARGUS_JUDGE_MODEL", "from-env")
        cfg = AgentArgusConfig.from_env(judge_model="explicit")
        assert cfg.judge_model == "explicit"

    def test_unknown_override_raises(self) -> None:
        with pytest.raises(TypeError):
            AgentArgusConfig.from_env(nonexistent=True)

    def test_bad_float_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENTARGUS_COST_CEILING_USD", "not-a-number")
        cfg = AgentArgusConfig.from_env()
        assert cfg.cost_ceiling_usd is None


class TestJudgeProtocol:
    def test_duck_typed_implementation_satisfies_protocol(self) -> None:
        class FakeJudge:
            def complete(self, prompt: str) -> str:
                return "ok"

        assert isinstance(FakeJudge(), Judge)

    def test_missing_method_fails_protocol(self) -> None:
        class NotAJudge:
            pass

        assert not isinstance(NotAJudge(), Judge)
