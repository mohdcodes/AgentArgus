"""Tests for AgentArgusConfig (env resolution + overrides + Judge protocol)."""

from __future__ import annotations

import pytest
from agentargus._internal.exceptions import ConfigError
from agentargus.config import AgentArgusConfig, Judge, batch_complete


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

    def test_malformed_cost_ceiling_fails_fast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # #9: a safety limit must not silently fall back to a default.
        monkeypatch.setenv("AGENTARGUS_COST_CEILING_USD", "not-a-number")
        with pytest.raises(ConfigError):
            AgentArgusConfig.from_env()


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


class TestBatchComplete:
    def test_falls_back_to_loop_without_complete_batch(self) -> None:
        # #6: simple adapters without complete_batch still work via the loop.
        calls: list[str] = []

        class SimpleJudge:
            def complete(self, prompt: str) -> str:
                calls.append(prompt)
                return prompt.upper()

        out = batch_complete(SimpleJudge(), ["a", "b"])
        assert out == ["A", "B"]
        assert calls == ["a", "b"]

    def test_uses_complete_batch_when_available(self) -> None:
        class BatchingJudge:
            def complete(self, prompt: str) -> str:  # pragma: no cover - not used
                raise AssertionError("should not be called")

            def complete_batch(self, prompts: list[str]) -> list[str]:
                return [p * 2 for p in prompts]

        out = batch_complete(BatchingJudge(), ["x", "y"])
        assert out == ["xx", "yy"]
