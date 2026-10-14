"""评测历史、数据隔离以及参数默认值的回归测试。"""

import json
import sys
from pathlib import Path

import pytest

from app.modules.knowledge import records
from scripts.evaluate_rag import parse_arguments


def test_default_retrieval_dataset_is_current_100(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["evaluate_rag"])
    assert parse_arguments().cases == records.DEFAULT_CASES_PATH
    assert len(json.loads(records.DEFAULT_CASES_PATH.read_text(encoding="utf-8"))) == 100


def test_records_preserve_rounds_and_load_actual_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(records, "RECORDS_ROOT", tmp_path)
    for record_id in ("first", "second"):
        output = tmp_path / record_id
        output.mkdir()
        (output / "evaluation_report.json").write_text(json.dumps({"cases": 100}), encoding="utf-8")
        records.save_record_metadata(output, records.DEFAULT_CASES_PATH)
    output = tmp_path / "second"
    (output / "runtime_report.json").write_text(json.dumps({"cases": 1}), encoding="utf-8")
    (output / "runtime_results.jsonl").write_text(json.dumps({"id": "case", "answer": "实际答案"}, ensure_ascii=False), encoding="utf-8")
    records.save_record_metadata(output, records.DEFAULT_CASES_PATH)
    history = records.list_records()
    assert [item["id"] for item in history] == ["second", "first"]
    assert records.read_record("second")["runtime_results"][0]["answer"] == "实际答案"
    assert records.read_record("first")["runtime"] is None


def test_old_dataset_and_path_traversal_are_excluded(tmp_path, monkeypatch):
    monkeypatch.setattr(records, "RECORDS_ROOT", tmp_path)
    old_cases = tmp_path / "old.json"
    old_cases.write_text("[]", encoding="utf-8")
    records.save_record_metadata(tmp_path / "old", old_cases)
    assert records.list_records() == []
    with pytest.raises(FileNotFoundError):
        records.read_record("../outside")


def test_partial_resume_keeps_last_complete_report_visible(tmp_path, monkeypatch):
    monkeypatch.setattr(records, "RECORDS_ROOT", tmp_path)
    output = tmp_path / "resuming"
    archive = output / "attempt-first"
    archive.mkdir(parents=True)
    for directory in (output, archive):
        (directory / "runtime_report.json").write_text('{"cases":2}', encoding="utf-8")
    (output / "runtime_results.jsonl").write_text('{"id":"partial"}\n', encoding="utf-8")
    (archive / "runtime_results.jsonl").write_text('{"id":"one"}\n{"id":"two"}\n', encoding="utf-8")
    records.save_record_metadata(output, records.DEFAULT_CASES_PATH)
    assert [item["id"] for item in records.read_record("resuming")["runtime_results"]] == ["one", "two"]
    (output / "runtime_record.json").write_text('{"runtime":{"cases":1},"runtime_results":[{"id":"snapshot"}]}', encoding="utf-8")
    assert records.read_record("resuming")["runtime_results"] == [{"id": "snapshot"}]
