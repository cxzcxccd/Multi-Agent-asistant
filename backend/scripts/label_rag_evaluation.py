"""使用两遍模型审查为 JDDC Query 生成可追溯的 RAG 候选标注。"""

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.ai.model_client import ModelClient, ModelInvocationError, create_model_client
from app.modules.knowledge.loader import load_knowledge_directory

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = BACKEND_ROOT / "data" / "evaluation" / "rag_annotations_500.jsonl"
DEFAULT_OUTPUT = BACKEND_ROOT / "data" / "evaluation" / "rag_annotations_500_ai.jsonl"
KNOWLEDGE_PATH = BACKEND_ROOT / "data" / "knowledge"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="两遍审查JDDC项目RAG标注")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--end-index", type=int)
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def knowledge_context() -> tuple[str, set[str]]:
    chunks = load_knowledge_directory(KNOWLEDGE_PATH)
    blocks: list[str] = []
    sources: set[str] = set()
    for chunk in chunks:
        sources.add(chunk.source)
        blocks.append(f"[来源: {chunk.source}]\n{chunk.content}")
    return "\n\n".join(blocks), sources


def parse_json_array(text: str) -> list[dict[str, Any]]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    payload = json.loads(cleaned)
    if not isinstance(payload, list):
        raise ValueError("标注模型没有返回JSON数组")
    return payload


def invoke_json_array(
    client: ModelClient,
    system_prompt: str,
    user_prompt: str,
) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    for _attempt in range(3):
        try:
            response = client.invoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt),
                ]
            )
            return parse_json_array(str(response.content))
        except (json.JSONDecodeError, ValueError, ModelInvocationError) as error:
            last_error = error
            if not isinstance(error, ModelInvocationError):
                user_prompt += "\n上一次输出格式无效。请只返回完整JSON数组。"
            time.sleep(2)
    raise ValueError("连续三次未获得有效JSON数组") from last_error


def validate_batch(
    original_batch: list[dict[str, Any]],
    labeled_batch: list[dict[str, Any]],
    valid_sources: set[str],
) -> None:
    original_ids = [record["id"] for record in original_batch]
    labeled_ids = [record.get("id") for record in labeled_batch]
    if labeled_ids != original_ids:
        raise ValueError("模型返回的标注ID或顺序与输入不一致")
    for record in labeled_batch:
        if not isinstance(record.get("should_answer"), bool):
            raise ValueError(f"{record['id']} 缺少布尔类型should_answer")
        sources = record.get("expected_sources")
        if not isinstance(sources, list) or not set(sources).issubset(valid_sources):
            raise ValueError(f"{record['id']} 返回了不存在的知识来源")
        if record["should_answer"] != bool(sources):
            raise ValueError(f"{record['id']} 的回答标记与来源不一致")
        if not str(record.get("reference_answer", "")).strip():
            raise ValueError(f"{record['id']} 缺少参考答案")


def label_batch(
    client: ModelClient,
    batch: list[dict[str, Any]],
    context: str,
    valid_sources: set[str],
) -> list[dict[str, Any]]:
    system_prompt = (
        "你是RAG数据标注员。只依据给定知识块判断每个独立Query是否可回答。"
        "不能依赖未提供的历史对话、订单数据、商品价格、平台操作或常识补全。"
        "部分支持也必须标为不可回答。只返回JSON数组。"
    )
    output_schema = (
        '[{"id":"原ID","query":"原问题","category":"原分类",'
        '"should_answer":true,"expected_sources":["真实来源ID"],'
        '"reference_answer":"完全由来源支持的简洁答案","confidence":0.0到1.0,'
        '"review_note":"判断依据"}]'
    )
    payload = [
        {"id": item["id"], "query": item["query"], "category": item["category"]}
        for item in batch
    ]
    user_prompt = (
        f"知识块：\n{context}\n\n待标注Query：\n"
        f"{json.dumps(payload, ensure_ascii=False)}\n\n输出格式：{output_schema}\n"
        "不可回答时expected_sources必须为空，reference_answer必须为‘根据现有知识库无法确定。’"
    )
    last_error: ValueError | None = None
    for _attempt in range(3):
        labeled = invoke_json_array(client, system_prompt, user_prompt)
        try:
            validate_batch(batch, labeled, valid_sources)
            return labeled
        except ValueError as error:
            last_error = error
            user_prompt += (
                f"\n上一次标注校验失败：{error}。"
                f"expected_sources只能从这些ID选择：{sorted(valid_sources)}。请修正全部记录。"
            )
    raise ValueError("候选标注连续三次未通过业务校验") from last_error


def verify_batch(
    client: ModelClient,
    original_batch: list[dict[str, Any]],
    proposed_batch: list[dict[str, Any]],
    context: str,
    valid_sources: set[str],
) -> list[dict[str, Any]]:
    system_prompt = (
        "你是第二位独立RAG标注审核员。逐条核对候选标注，发现错误时直接修正。"
        "标准从严：答案必须由知识块完整支持；需要对话历史、实时数据、商品详情或操作流程的一律不可回答。"
        "不要相信候选分类，只核对Query与原文。只返回JSON数组。"
    )
    user_prompt = (
        f"知识块：\n{context}\n\n第一遍候选标注：\n"
        f"{json.dumps(proposed_batch, ensure_ascii=False)}\n\n"
        "保持原ID、Query和category；返回should_answer、expected_sources、reference_answer、confidence和review_note。"
        "不可回答时使用固定答案‘根据现有知识库无法确定。’"
    )
    last_error: ValueError | None = None
    for _attempt in range(3):
        verified = invoke_json_array(client, system_prompt, user_prompt)
        try:
            validate_batch(original_batch, verified, valid_sources)
            break
        except ValueError as error:
            last_error = error
            user_prompt += (
                f"\n上一次审核结果校验失败：{error}。"
                f"expected_sources只能从这些ID选择：{sorted(valid_sources)}。请修正全部记录。"
            )
    else:
        raise ValueError("审核标注连续三次未通过业务校验") from last_error
    for record in verified:
        record["review_status"] = "ai_verified"
        record["review_method"] = "two_pass_llm_review"
    return verified


def run_labeling(
    client: ModelClient,
    input_path: Path,
    output_path: Path,
    batch_size: int,
    start_index: int = 0,
    end_index: int | None = None,
) -> None:
    all_records = read_jsonl(input_path)
    records = all_records[start_index:end_index]
    context, valid_sources = knowledge_context()
    completed: list[dict[str, Any]] = []
    if output_path.exists():
        completed = read_jsonl(output_path)
    completed_ids = {record["id"] for record in completed}

    pending = [record for record in records if record["id"] not in completed_ids]
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        proposed = label_batch(client, batch, context, valid_sources)
        verified = verify_batch(client, batch, proposed, context, valid_sources)
        completed.extend(verified)
        write_jsonl(output_path, completed)
        print(f"已完成分片 {len(completed)}/{len(records)} 条两遍标注")


def main() -> None:
    arguments = parse_arguments()
    client = create_model_client(tools=[])
    run_labeling(
        client,
        arguments.input,
        arguments.output,
        arguments.batch_size,
        arguments.start_index,
        arguments.end_index,
    )


if __name__ == "__main__":
    main()
