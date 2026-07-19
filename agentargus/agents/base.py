"""The ``BaseAgent`` abstraction (spec §6.1).

``BaseAgent`` defines the contract every agent and wrapper implements. The real
work is async (``arun``) so that reliability, tracing, cost, and HITL are all
written once on a single code path (the "async-core, sync-wraps" decision). The
synchronous ``run`` is a thin driver over ``arun`` — it exists only for callers
who are not in an async context.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from agentargus.core import RunResult

__all__ = ["BaseAgent"]


class BaseAgent(ABC):
    """Abstract base for anything that can be run and produce a ``RunResult``."""

    @abstractmethod
    async def arun(self, input: Any) -> RunResult:
        """Asynchronously run the agent and return a ``RunResult``.

        This is the single real implementation point. Subclasses put all of
        their orchestration here; ``run`` borrows it.
        """
        raise NotImplementedError

    def run(self, input: Any) -> RunResult:
        """Synchronously run the agent by driving ``arun`` to completion.

        Deliberately refuses to run inside an already-running event loop rather
        than nesting loops (which deadlocks or requires fragile hacks). A caller
        already in async code should ``await agent.arun(...)`` directly. The
        error message says exactly that.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — the normal synchronous case.
            return asyncio.run(self.arun(input))
        raise RuntimeError(
            "Agent.run() cannot be called from within a running event loop. "
            "Use 'await agent.arun(input)' instead."
        )
