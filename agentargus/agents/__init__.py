"""Agents: the ``BaseAgent`` contract and the ``Agent`` facade."""

from agentargus.agents.agent import Agent
from agentargus.agents.base import BaseAgent
from agentargus.agents.checkpoint_store import (
    Checkpointer,
    InMemoryCheckpointer,
    SqliteCheckpointer,
)
from agentargus.agents.patterns import Handoff, LLMRouter, Router, SupervisorAgent
from agentargus.agents.recorder import Recorder, record_step, record_tool_call

__all__ = [
    "BaseAgent",
    "Agent",
    "Recorder",
    "record_tool_call",
    "record_step",
    "SupervisorAgent",
    "Handoff",
    "Router",
    "LLMRouter",
    "Checkpointer",
    "SqliteCheckpointer",
    "InMemoryCheckpointer",
]
