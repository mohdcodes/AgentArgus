"""LLM adapters for the examples.

AgentArgus core ships NO LLM client (framework-agnostic, minimal deps — spec
§1/§9). The examples inject one via the ``Judge`` protocol: a real Anthropic
adapter that reads ``ANTHROPIC_API_KEY`` from the environment, and a ``MockJudge``
so the examples' wiring can be exercised without a key.

Never hardcode a key. Set it in a gitignored ``.env`` or your shell env.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path


def load_dotenv(path: str | Path | None = None) -> None:
    """Minimal zero-dependency .env loader (KEY=VALUE lines).

    Loads the repo-root .env into ``os.environ`` (without overwriting existing
    vars) so examples pick up ANTHROPIC_API_KEY. Avoids a python-dotenv dep.
    """
    env_path = Path(path) if path else Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


DEFAULT_MODEL = os.environ.get("AGENTARGUS_EXAMPLE_MODEL", "claude-opus-4-8")


class AnthropicJudge:
    """A ``Judge`` (``.complete(prompt) -> str``) backed by the Anthropic SDK.

    Also usable as the examples' LLM for generating answers, not only judging.
    Reads the API key from ``ANTHROPIC_API_KEY``.
    """

    def __init__(self, model: str = DEFAULT_MODEL, *, max_tokens: int = 1024) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - optional example dep
            raise ImportError(
                "The examples need the 'anthropic' package. "
                "Install with:  pip install 'agentargus[examples]'"
            ) from exc
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Put it in a .env file or your "
                "shell environment before running this example with a real model."
            )
        self._client = anthropic.Anthropic(api_key=key)
        self._model = model
        self._max_tokens = max_tokens
        self.last_usage: dict[str, int] | None = None

    def complete(self, prompt: str) -> str:
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        # Record usage so the example can feed it to CostTracker.add_usage.
        self.last_usage = {
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
        }
        # Concatenate text blocks of the response.
        return "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        )


class MockJudge:
    """A keyless stand-in. Returns a canned or rule-based response.

    Lets the examples run (and their wiring be verified) with no API key. Answers
    and scores are synthetic — for a real quality signal, use ``AnthropicJudge``.
    """

    def __init__(self, responder: Callable[[str], str] | None = None) -> None:
        self._responder = responder or self._default
        self.last_usage = {"input_tokens": 50, "output_tokens": 25}

    @staticmethod
    def _default(prompt: str) -> str:
        import json

        low = prompt.lower()
        # Return JSON shaped for whichever metric is asking.
        if "claims" in low and "supported" in low:
            return json.dumps({"claims": ["c1", "c2"], "supported": [True, True]})
        if "relevance" in low:
            return json.dumps({"relevance": 0.9})
        if "relevant" in low:
            return json.dumps({"relevant": [True, True, False]})
        if "coherence" in low:
            return json.dumps({"coherence": 0.85})
        if "questions" in low:
            return json.dumps({"questions": ["q1", "q2", "q3"]})
        if '"calls"' in prompt or "tools:" in low:
            # Mock a tool plan based on keywords in the question.
            calls = []
            if any(c.isdigit() for c in prompt):
                calls.append({"tool": "calculator", "arg": "100 + 50"})
            if "search" in low:
                calls.append({"tool": "web_search", "arg": "topic"})
            if "time" in low:
                calls.append({"tool": "current_time", "arg": None})
            return json.dumps({"calls": calls or [{"tool": "web_search", "arg": "x"}]})
        # Otherwise behave like an answer generator.
        return "This is a mock answer generated without calling a real model."

    def complete(self, prompt: str) -> str:
        return self._responder(prompt)


def get_llm(*, prefer_real: bool = True) -> object:
    """Return an AnthropicJudge if a key is set, else a MockJudge (with a note)."""
    load_dotenv()  # pull .env into the environment first
    if prefer_real and os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicJudge()
    print(
        "[examples] ANTHROPIC_API_KEY not set - using MockJudge. "
        "Answers/scores are synthetic. Set the key for real results.\n"
    )
    return MockJudge()
