"""新版 JDDC 100 条评测的默认数据与持久化记录。"""

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CASES_PATH = BACKEND_ROOT / "data/evaluation/jddc_test_current_100.json"
RECORDS_ROOT = BACKEND_ROOT / "data/evaluation/results/current-100"


def new_record_directory() -> Path:
    return RECORDS_ROOT / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:12])


def save_record_metadata(output: Path, cases_path: Path) -> None:
    """只将新版数据的记录提供给页面。"""
    if hashlib.sha256(cases_path.read_bytes()).hexdigest() != hashlib.sha256(DEFAULT_CASES_PATH.read_bytes()).hexdigest():
        return
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    metadata = {
        "id": output.name,
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_name": "JDDC 当前知识库 100 条",
        "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "cases": len(cases),
        "annotation_status": "ai_verified（两遍模型核验，尚非人工金标准）",
        "has_retrieval": (output / "evaluation_report.json").exists(),
        "has_runtime": (output / "runtime_report.json").exists(),
    }
    output.mkdir(parents=True, exist_ok=True)
    temporary = output / ("record-" + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output / "record.json")


def list_records() -> list[dict]:
    records = []
    for path in RECORDS_ROOT.glob("*/record.json"):
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
            if metadata["cases_sha256"] == hashlib.sha256(DEFAULT_CASES_PATH.read_bytes()).hexdigest():
                records.append(metadata)
        except (OSError, ValueError, KeyError):
            continue
    return sorted(records, key=lambda item: item["created_at"], reverse=True)


def read_record(record_id: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", record_id):
        raise FileNotFoundError(record_id)
    directory = RECORDS_ROOT / record_id
    metadata = json.loads((directory / "record.json").read_text(encoding="utf-8"))
    if metadata["cases_sha256"] != hashlib.sha256(DEFAULT_CASES_PATH.read_bytes()).hexdigest():
        raise FileNotFoundError(record_id)
    result = {**metadata, "retrieval": None, "runtime": None, "runtime_results": []}
    if metadata["has_retrieval"]:
        result["retrieval"] = json.loads((directory / "evaluation_report.json").read_text(encoding="utf-8"))
    if metadata["has_runtime"]:
        snapshot_path = directory / "runtime_record.json"
        if snapshot_path.exists():
            result.update(json.loads(snapshot_path.read_text(encoding="utf-8")))
        else:
            candidates = [directory, *sorted(directory.glob("attempt-*"), reverse=True)]
            for candidate in candidates:
                try:
                    report = json.loads((candidate / "runtime_report.json").read_text(encoding="utf-8"))
                    rows = [json.loads(line) for line in (candidate / "runtime_results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
                except (OSError, ValueError):
                    continue
                if len(rows) == report["cases"]:
                    result.update(runtime=report, runtime_results=rows)
                    break
            else:
                raise ValueError("评测明细尚未完整保存")
    return result
