"""Tests for EvalDataset (spec §8: load dispatch, jsonl round-trip, validation)."""

from __future__ import annotations

from pathlib import Path

import pytest
from agentargus._internal.exceptions import ConfigError
from agentargus.eval import EvalDataset

FIXTURE = Path(__file__).parent.parent / "fixtures" / "golden_dataset.jsonl"


class TestLoadDispatch:
    def test_load_jsonl_path(self) -> None:
        ds = EvalDataset().load(str(FIXTURE))
        assert len(ds) == 3
        assert ds.cases[0].question == "What is the capital of France?"
        assert ds.cases[1].contexts == ("Hamlet is a play by William Shakespeare.",)

    def test_load_list_of_records(self) -> None:
        ds = EvalDataset().load([{"question": "a"}, {"question": "b"}])
        assert len(ds) == 2

    def test_load_single_dict(self) -> None:
        ds = EvalDataset().load({"question": "solo", "reference": "r"})
        assert len(ds) == 1
        assert ds.cases[0].reference == "r"

    def test_load_json_array(self, tmp_path: Path) -> None:
        p = tmp_path / "cases.json"
        p.write_text('[{"question": "x"}, {"question": "y"}]', encoding="utf-8")
        assert len(EvalDataset().load(str(p))) == 2


class TestValidation:
    def test_missing_question_raises_with_index(self) -> None:
        with pytest.raises(ConfigError, match="index 1"):
            EvalDataset().load([{"question": "ok"}, {"reference": "no question"}])

    def test_empty_question_raises(self) -> None:
        with pytest.raises(ConfigError, match="question"):
            EvalDataset().load([{"question": "   "}])

    def test_missing_file_raises(self) -> None:
        with pytest.raises(ConfigError, match="not found"):
            EvalDataset().load("does_not_exist.jsonl")

    def test_bad_extension_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "cases.txt"
        p.write_text("stuff", encoding="utf-8")
        with pytest.raises(ConfigError, match="Unsupported"):
            EvalDataset().load(str(p))

    def test_malformed_jsonl_line_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.jsonl"
        p.write_text('{"question": "ok"}\nNOT JSON\n', encoding="utf-8")
        with pytest.raises(ConfigError, match="line 2"):
            EvalDataset().load(str(p))


class TestFromJsonl:
    def test_from_jsonl_classmethod(self) -> None:
        ds = EvalDataset.from_jsonl(FIXTURE)
        assert len(ds) == 3
