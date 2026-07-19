"""Tests for EvalRunner (spec §8: batch, failure capture, case->metric wiring)."""

from __future__ import annotations

import json
from typing import Any

from agentargus import Agent, EvalDataset, EvalRunner, EvalSuite, Faithfulness


class FakeJudge:
    def complete(self, prompt: str) -> str:
        return json.dumps({"claims": ["a"], "supported": [True]})


def _suite() -> EvalSuite:
    return EvalSuite([Faithfulness(judge=FakeJudge())])


class TestBatch:
    def test_runs_all_cases_and_scores(self) -> None:
        dataset = EvalDataset().load([{"question": "q1"}, {"question": "q2"}, {"question": "q3"}])
        agent = Agent(lambda q: f"answer to {q}")
        report = EvalRunner(concurrency=2).run(agent, dataset, _suite())
        assert len(report.case_results) == 3
        assert all("faithfulness" in cr.scores for cr in report.case_results)

    def test_case_fields_reach_metrics(self) -> None:
        # The case's contexts must appear in the scoring view metadata.
        seen: dict[str, Any] = {}

        class Spy(Faithfulness):
            def _score(self, inp: Any) -> float:
                seen["contexts"] = inp.contexts
                seen["reference"] = inp.reference
                return 1.0

        dataset = EvalDataset().load(
            [{"question": "q", "reference": "ref", "contexts": ["ctx-from-case"]}]
        )
        suite = EvalSuite([Spy(judge=FakeJudge())])
        EvalRunner().run(Agent(lambda q: "a"), dataset, suite)
        assert seen["contexts"] == ("ctx-from-case",)
        assert seen["reference"] == "ref"


class TestFailureCapture:
    def test_one_failing_case_does_not_sink_batch(self) -> None:
        def flaky(q: str) -> str:
            if q == "boom":
                raise RuntimeError("kaboom")
            return "ok"

        dataset = EvalDataset().load([{"question": "fine"}, {"question": "boom"}])
        report = EvalRunner().run(Agent(flaky), dataset, _suite())
        assert len(report.case_results) == 2
        failed = [cr for cr in report.case_results if cr.failed]
        assert len(failed) == 1
        assert "kaboom" in failed[0].error


class TestConcurrencyGuard:
    def test_run_inside_loop_raises(self) -> None:
        import asyncio

        async def main() -> None:
            dataset = EvalDataset().load([{"question": "q"}])
            try:
                EvalRunner().run(Agent(lambda q: q), dataset, _suite())
            except RuntimeError as exc:
                assert "running event loop" in str(exc)
                return
            raise AssertionError("expected RuntimeError")

        asyncio.run(main())
