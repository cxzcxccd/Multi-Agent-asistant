"""从 DialogueCSE 发布的 JDDC 处理数据中构建项目相关评测子集。"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path


CATEGORY_INTENTS = {
    "return_policy": {
        "保修返修及退换货政策",
        "返修退换货处理周期",
        "返修退换货拆包装",
        "返回方式",
        "返回地址",
        "拒收",
        "发错货",
        "物流损",
        "返修退换货发票",
        "售后运费",
    },
    "warranty_policy": {
        "保修期保质期",
        "售后维修点查询",
        "检测单咨询",
        "延保服务",
        "联系售后",
        "正品保障",
        "生产日期",
    },
    "shipping_policy": {
        "预约配送时间",
        "物流信息不正确",
        "什么时间出库",
        "配送周期",
        "配送工作时间",
        "订单签收异常",
        "物流全程跟踪",
        "联系配送",
        "是否送货上门",
        "提前配送",
        "发货检查",
        "能否配送",
    },
    "payment_policy": {
        "退款到哪儿",
        "正常退款周期",
        "退款异常",
        "在哪里查询退款",
        "支付方式",
        "支付到账时间",
        "在线支付",
        "货到付款",
        "取消订单白条处理",
        "申请退款",
        "取消退款",
    },
    "product_guide": {
        "使用咨询",
        "属性咨询",
        "包装清单",
        "商品比较",
        "商品检索",
        "外包装",
        "为什么显示无货",
        "库存状态",
        "商品价格咨询",
    },
}


@dataclass(frozen=True, slots=True)
class SourceRow:
    """JDDC select.txt 中一条可追溯的查询记录。"""

    source_split: str
    source_line: int
    original_id: str
    query: str
    intent: str
    positive_candidate_ids: list[int]
    negative_candidate_ids: list[int]


def parse_candidate_ids(value: str) -> list[int]:
    """把逗号分隔的候选编号转换为整数列表。"""

    candidate_ids: list[int] = []
    for item in value.split(","):
        normalized = item.strip()
        if normalized:
            candidate_ids.append(int(normalized))
    return candidate_ids


def load_rows(source_root: Path) -> list[SourceRow]:
    """读取JDDC开发集和测试集中的检索选择任务。"""

    rows: list[SourceRow] = []
    for split in ("dev", "test"):
        path = source_root / split / "select.txt"
        lines = path.read_text(encoding="utf-8").splitlines()
        for line_number, line in enumerate(lines, start=1):
            fields = line.split("\t")
            if len(fields) != 5:
                continue
            query = fields[1].strip()
            if not query:
                continue
            rows.append(
                SourceRow(
                    source_split=split,
                    source_line=line_number,
                    original_id=fields[0],
                    query=query,
                    intent=fields[2],
                    positive_candidate_ids=parse_candidate_ids(fields[3]),
                    negative_candidate_ids=parse_candidate_ids(fields[4]),
                )
            )
    return rows


def stable_sort_key(row: SourceRow) -> tuple[str, str, int]:
    """使用Query哈希稳定抽样，避免依赖源文件的主题排列。"""

    digest = hashlib.sha256(row.query.encode("utf-8")).hexdigest()
    return digest, row.source_split, row.source_line


def select_balanced_rows(
    rows: list[SourceRow],
    per_category: int,
) -> list[dict[str, object]]:
    """按项目五类知识主题等量筛选，并对Query去重。"""

    selected: list[dict[str, object]] = []
    used_queries: set[str] = set()

    for category, accepted_intents in CATEGORY_INTENTS.items():
        candidates: list[SourceRow] = []
        for row in rows:
            if row.intent in accepted_intents:
                candidates.append(row)
        candidates.sort(key=stable_sort_key)

        category_count = 0
        for row in candidates:
            normalized_query = row.query.strip()
            if normalized_query in used_queries:
                continue

            selected.append(
                {
                    "id": "",
                    "query": row.query,
                    "jddc_intent": row.intent,
                    "project_category": category,
                    "project_category_reviewed": False,
                    "expected_sources": [],
                    "rag_label_status": "unlabeled",
                    "recommended_evaluation": "query_intent_and_retrieval_candidate",
                    "source": {
                        "dataset": "JDDC",
                        "distribution": "DialogueCSE",
                        "split": row.source_split,
                        "file": f"{row.source_split}/select.txt",
                        "line": row.source_line,
                        "original_id": row.original_id,
                    },
                    "original_positive_candidate_ids": row.positive_candidate_ids,
                    "original_negative_candidate_ids": row.negative_candidate_ids,
                }
            )
            used_queries.add(normalized_query)
            category_count += 1
            if category_count == per_category:
                break

        if category_count != per_category:
            raise ValueError(
                f"分类 {category} 只有 {category_count} 条去重样本，"
                f"无法满足 {per_category} 条的要求"
            )

    for index, item in enumerate(selected, start=1):
        item["id"] = f"JDDC-PROJECT-{index:04d}"
    return selected


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    """写入便于逐行读取的UTF-8 JSONL文件。"""

    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            serialized = json.dumps(row, ensure_ascii=False)
            file.write(serialized)
            file.write("\n")


def build_manifest(rows: list[dict[str, object]]) -> dict[str, object]:
    """生成数量、分类和标注状态摘要。"""

    category_counts: dict[str, int] = {}
    intent_counts: dict[str, int] = {}
    for row in rows:
        category = str(row["project_category"])
        intent = str(row["jddc_intent"])
        category_counts[category] = category_counts.get(category, 0) + 1
        intent_counts[intent] = intent_counts.get(intent, 0) + 1

    return {
        "dataset": "JDDC project subset",
        "records": len(rows),
        "category_counts": category_counts,
        "intent_counts": dict(sorted(intent_counts.items())),
        "rag_ground_truth_complete": False,
        "license": "Apache-2.0",
    }


def write_retrieval_benchmark(
    output_root: Path,
    source_rows: list[SourceRow],
    selected_rows: list[dict[str, object]],
) -> None:
    """写出标准的corpus、queries和qrels，供独立检索评测使用。"""

    corpus_path = output_root / "corpus.jsonl"
    with corpus_path.open("w", encoding="utf-8", newline="\n") as file:
        for row in source_rows:
            item = {
                "id": f"jddc-{row.source_split}-{row.original_id}",
                "text": row.query,
                "jddc_intent": row.intent,
                "source_split": row.source_split,
                "source_line": row.source_line,
            }
            file.write(json.dumps(item, ensure_ascii=False))
            file.write("\n")

    queries_path = output_root / "queries.jsonl"
    with queries_path.open("w", encoding="utf-8", newline="\n") as file:
        for row in selected_rows:
            item = {
                "id": row["id"],
                "text": row["query"],
                "jddc_intent": row["jddc_intent"],
                "project_category": row["project_category"],
                "project_category_reviewed": False,
            }
            file.write(json.dumps(item, ensure_ascii=False))
            file.write("\n")

    qrels_path = output_root / "qrels.tsv"
    hard_negatives_path = output_root / "hard_negatives.tsv"
    with (
        qrels_path.open("w", encoding="utf-8", newline="\n") as qrels_file,
        hard_negatives_path.open(
            "w",
            encoding="utf-8",
            newline="\n",
        ) as negatives_file,
    ):
        qrels_file.write("query_id\tcorpus_id\trelevance\n")
        negatives_file.write("query_id\tcorpus_id\n")
        for row in selected_rows:
            source = row["source"]
            if not isinstance(source, dict):
                raise TypeError("来源信息必须是字典")
            split = str(source["split"])
            query_id = str(row["id"])

            positive_ids = row["original_positive_candidate_ids"]
            if not isinstance(positive_ids, list):
                raise TypeError("正样本候选编号必须是列表")
            for candidate_id in positive_ids:
                corpus_id = f"jddc-{split}-{candidate_id}"
                qrels_file.write(f"{query_id}\t{corpus_id}\t1\n")

            negative_ids = row["original_negative_candidate_ids"]
            if not isinstance(negative_ids, list):
                raise TypeError("负样本候选编号必须是列表")
            for candidate_id in negative_ids:
                corpus_id = f"jddc-{split}-{candidate_id}"
                negatives_file.write(f"{query_id}\t{corpus_id}\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--per-category", type=int, default=100)
    arguments = parser.parse_args()

    rows = load_rows(arguments.source_root)
    selected = select_balanced_rows(rows, arguments.per_category)
    arguments.output_root.mkdir(parents=True, exist_ok=True)

    dataset_path = arguments.output_root / "jddc_project_eval_500.jsonl"
    manifest_path = arguments.output_root / "manifest.json"
    write_jsonl(dataset_path, selected)
    manifest = build_manifest(selected)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    license_source = arguments.source_root / "LICENSE-2.0.txt"
    shutil.copyfile(license_source, arguments.output_root / "LICENSE-2.0.txt")
    write_retrieval_benchmark(arguments.output_root, rows, selected)


if __name__ == "__main__":
    main()
