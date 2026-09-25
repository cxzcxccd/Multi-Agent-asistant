"""JDDC人工标注模板和分层划分测试。"""

import json
from pathlib import Path

import pytest

from scripts.prepare_rag_evaluation import build_splits, initialize_annotations


def write_jsonl(path: Path, records: list[dict]) -> None:
    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_build_splits_requires_human_review(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    annotations = tmp_path / "annotations.jsonl"
    write_jsonl(
        source,
        [
            {
                "id": f"case-{index}",
                "query": f"问题{index}",
                "project_category": "warranty_policy",
            }
            for index in range(500)
        ],
    )
    initialize_annotations(source, annotations)

    with pytest.raises(ValueError, match="尚未人工复核"):
        build_splits(annotations, tmp_path / "splits")


def test_build_splits_creates_300_100_100_stratified_files(tmp_path: Path) -> None:
    categories = [
        "return_policy",
        "warranty_policy",
        "shipping_policy",
        "payment_policy",
        "product_guide",
    ]
    records: list[dict] = []
    for category in categories:
        for index in range(100):
            records.append(
                {
                    "id": f"{category}-{index}",
                    "query": f"{category}问题{index}",
                    "category": category,
                    "should_answer": True,
                    "expected_sources": [f"{category}.md#章节"],
                    "reference_answer": "人工参考答案",
                    "review_status": "reviewed",
                }
            )
    annotations = tmp_path / "annotations.jsonl"
    write_jsonl(annotations, records)

    build_splits(annotations, tmp_path / "splits")

    development = json.loads((tmp_path / "splits" / "development.json").read_text("utf-8"))
    validation = json.loads((tmp_path / "splits" / "validation.json").read_text("utf-8"))
    test = json.loads((tmp_path / "splits" / "test.json").read_text("utf-8"))
    assert len(development) == 300
    assert len(validation) == 100
    assert len(test) == 100
