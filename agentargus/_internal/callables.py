"""Shared normalisation of a wrap target into one async callable.

Both ``Agent.wrap`` (Module 1) and ``FallbackChain`` (Module 4) need to turn an
arbitrary target — a ``BaseAgent``, a sync function, or an async function — into
a uniform ``async (input) -> output`` callable. One home for that logic so the
two do not duplicate the sync/async/BaseAgent handling (spec: one behaviour, one
home).
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

__all__ = ["to_async_callable"]


def to_async_callable(target: Any) -> Callable[[Any], Awaitable[Any]]:
    """Normalise ``target`` into ``async (input) -> output``.

    - ``BaseAgent`` → awaits ``target.arun(input)`` and returns its ``.output``.
    - async callable → awaited directly.
    - sync callable → run via ``asyncio.to_thread`` so it never blocks the loop.
    """
    # Imported lazily to avoid a core/agents import cycle.
    from agentargus.agents.base import BaseAgent

    if isinstance(target, BaseAgent):

        async def call_agent(inp: Any) -> Any:
            result = await target.arun(inp)
            return result.output

        return call_agent

    if not callable(target):
        raise TypeError(f"Target must be a BaseAgent or a callable, got {type(target).__name__!r}.")

    if inspect.iscoroutinefunction(target):

        async def call_async(inp: Any) -> Any:
            return await target(inp)

        return call_async

    async def call_sync(inp: Any) -> Any:
        return await asyncio.to_thread(target, inp)

    return call_sync
