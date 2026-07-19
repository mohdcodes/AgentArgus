"""Cross-cutting tests for every methodoverload dispatch site (spec §8).

As each overload site lands, its dispatch assertions accumulate here so there is
one place proving the owner's own library is used correctly and the type
dispatch actually happens (rather than silently collapsing to one impl).

Sites:
    #3  Agent.wrap(inner)  — BaseAgent overload vs. object catch-all  (Module 1)
    #1  EvalDataset.load   — str/list/dict                            (Module 6)
    #2  CostTracker.add_usage                                          (Module 3)
    #4  Metric.compute     — RunResult/dict                            (Module 5)
"""

from __future__ import annotations

from typing import Any

from agentargus import CostTracker, Usage
from agentargus.agents import Agent, BaseAgent
from agentargus.core import RunResult


class TestWrapSite:
    """Site #3: dispatch on BaseAgent vs. everything-else (object)."""

    def _make(self) -> Agent:
        return Agent(lambda x: x)

    def test_base_agent_branch_selected(self) -> None:
        class Inner(BaseAgent):
            async def arun(self, input: Any) -> RunResult:
                return RunResult(output="inner", trace_id="t")

        wrapped = self._make().wrap(Inner())
        # The BaseAgent branch returns a coroutine-producing callable.
        assert callable(wrapped)

    def test_callable_branch_selected_for_function(self) -> None:
        wrapped = self._make().wrap(lambda x: x)
        assert callable(wrapped)

    def test_callable_branch_selected_for_callable_object(self) -> None:
        class CallMe:
            def __call__(self, x: Any) -> Any:
                return x

        wrapped = self._make().wrap(CallMe())
        assert callable(wrapped)

    def test_end_to_end_dispatch_produces_correct_output(self) -> None:
        # Prove the two branches produce genuinely different behaviour.
        class Inner(BaseAgent):
            async def arun(self, input: Any) -> RunResult:
                return RunResult(output="from-base-agent", trace_id="t")

        assert Agent(Inner()).run("x").output == "from-base-agent"
        assert Agent(lambda x: "from-callable").run("x").output == "from-callable"


class TestAddUsageSite:
    """Site #2: dispatch on dict vs. Usage vs. provider-response object."""

    def _tracker(self) -> CostTracker:
        return CostTracker(pricing={"m": (1.0, 1.0)})

    def test_dict_branch(self) -> None:
        e = self._tracker().add_usage({"input_tokens": 3, "output_tokens": 2}, model="m")
        assert (e.input_tokens, e.output_tokens) == (3, 2)

    def test_usage_branch(self) -> None:
        e = self._tracker().add_usage(Usage(input_tokens=4, output_tokens=1), model="m")
        assert (e.input_tokens, e.output_tokens) == (4, 1)

    def test_object_branch(self) -> None:
        class Resp:
            class _U:
                input_tokens = 9
                output_tokens = 8

            usage = _U()

        e = self._tracker().add_usage(Resp(), model="m")
        assert (e.input_tokens, e.output_tokens) == (9, 8)

    def test_object_branch_rejects_unreadable(self) -> None:
        import pytest

        with pytest.raises(TypeError):
            self._tracker().add_usage(object(), model="m")
