"""创建 JDDC 人工标注表，并把已复核的500条案例分层划分。"""

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = (
    BACKEND_ROOT
    / "data"
    / "evaluation"
    / "external"
    / "JDDC-project-500"
    / "jddc_project_eval_500.jsonl"
)
DEFAULT_ANNOTATIONS = BACKEND_ROOT / "data" / "evaluation" / "rag_annotations_500.jsonl"
DEFAULT_OUTPUT = BACKEND_ROOT / "data" / "evaluation" / "splits"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="准备和划分项目RAG人工评测集")
    parser.add_argument("action", choices=["initialize", "build"])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for record in records:
        lines.append(json.dumps(record, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def initialize_annotations(source_path: Path, output_path: Path) -> None:
    if output_path.exists():
        raise FileExistsError(f"标注文件已存在，为避免覆盖人工结果已停止：{output_path}")
    source_records = read_jsonl(source_path)
    annotations: list[dict[str, Any]] = []
    for source in source_records:
        annotations.append(
            {
                "id": source["id"],
                "query": source["query"],
                "category": source["project_category"],
                "should_answer": None,
                "expected_sources": [],
                "reference_answer": "",
                "review_status": "unreviewed",
                "review_note": "",
            }
        )
    write_jsonl(output_path, annotations)
    print(f"已生成 {len(annotations)} 条人工标注模板：{output_path}")


def validate_annotation(record: dict[str, Any]) -> None:
    record_id = record.get("id", "unknown")
    if record.get("review_status") != "reviewed":
        raise ValueError(f"{record_id} 尚未人工复核")
    if not isinstance(record.get("should_answer"), bool):
        raise ValueError(f"{record_id} 缺少布尔类型 should_answer")
    expected_sources = record.get("expected_sources", [])
    if record["should_answer"] and not expected_sources:
        raise ValueError(f"{record_id} 可回答但没有 expected_sources")
    if not record["should_answer"] and expected_sources:
        raise ValueError(f"{record_id} 不可回答但仍填写了 expected_sources")
    if not str(record.get("reference_answer", "")).strip():
        raise ValueError(f"{record_id} 缺少 reference_answer")


def stable_order(record: dict[str, Any]) -> str:
    value = f"{record['category']}:{record['id']}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_splits(annotation_path: Path, output_directory: Path) -> None:
    records = read_jsonl(annotation_path)
    if len(records) != 500:
        raise ValueError(f"正式划分要求500条标注，当前为 {len(records)} 条")
    for record in records:
        validate_annotation(record)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["category"]].append(record)

    splits: dict[str, list[dict[str, Any]]] = {
        "development": [],
        "validation": [],
        "test": [],
    }
    for category, category_records in sorted(grouped.items()):
        ordered = sorted(category_records, key=stable_order)
        if len(ordered) != 100:
            raise ValueError(f"分类 {category} 应有100条，当前为 {len(ordered)} 条")
        splits["development"].extend(ordered[:60])
        splits["validation"].extend(ordered[60:80])
        splits["test"].extend(ordered[80:100])

    output_directory.mkdir(parents=True, exist_ok=True)
    for split_name, split_records in splits.items():
        output_path = output_directory / f"{split_name}.json"
        output_path.write_text(
            json.dumps(split_records, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"{split_name}: {len(split_records)} 条 -> {output_path}")


def main() -> None:
    arguments = parse_arguments()
    if arguments.action == "initialize":
        initialize_annotations(arguments.source, arguments.annotations)
        return
    build_splits(arguments.annotations, arguments.output)


if __name__ == "__main__":
    main()
