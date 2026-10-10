"""保留原100条测试Query，为当前知识库生成两遍模型核验标签。"""

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.ai.model_client import create_model_client
from app.core.config import settings
from app.modules.knowledge.loader import load_knowledge_directory
from scripts.label_rag_evaluation import invoke_json_array, validate_batch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = {"return_policy", "warranty_policy", "shipping_policy", "payment_policy", "product_guide"}


def validate_runtime_batch(original: list[dict], labeled: list[dict], valid_sources: set[str]) -> None:
    validate_batch(original, labeled, valid_sources)
    for source, result in zip(original, labeled, strict=True):
        if result.get("query") != source["query"] or result.get("category") != source["category"]:
            raise ValueError("不得改写Query或原数据分类")
        if result.get("expected_tool") not in {None, "search_knowledge"}:
            raise ValueError("未知预期工具")
        if "expected_tool" not in result:
            raise ValueError("缺少预期工具")
        allowed = result.get("allowed_categories")
        if not isinstance(allowed, list) or not set(allowed).issubset(CATEGORIES):
            raise ValueError("允许分类必须来自知识工具枚举")
        if result["should_answer"] and result["expected_tool"] != "search_knowledge":
            raise ValueError("有知识证据的问题必须查询知识工具")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=BACKEND_ROOT / "data/evaluation/splits/test.json")
    parser.add_argument("--output", type=Path, default=BACKEND_ROOT / "data/evaluation/jddc_test_current_100.json")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--model-timeout", type=float, default=90, help="只覆盖本次标注客户端，不修改线上配置")
    arguments = parser.parse_args()
    if not 1 <= arguments.workers <= 8 or not 1 <= arguments.model_timeout <= 300:
        parser.error("workers需为1到8，model-timeout需为1到300秒")
    original = json.loads(arguments.input.read_text(encoding="utf-8"))
    if len(original) != 100 or arguments.batch_size < 1:
        raise ValueError("需要原100条测试集，批大小必须大于零")
    chunks = load_knowledge_directory(BACKEND_ROOT / "data/knowledge")
    context_blocks = []
    valid_sources = set()
    for chunk in chunks:
        valid_sources.add(chunk.source)
        context_blocks.append(f"[来源:{chunk.source}][分类:{chunk.category}]\n{chunk.content}")
    context = "\n\n".join(context_blocks)
    knowledge_hash = hashlib.sha256(context.encode("utf-8")).hexdigest()
    completed = {}
    if arguments.output.exists():
        for item in json.loads(arguments.output.read_text(encoding="utf-8")):
            if item.get("knowledge_sha256") != knowledge_hash:
                raise ValueError("知识库已变化，禁止复用旧标注，请指定新输出")
            completed[item["id"]] = item
    annotation_settings = settings.model_copy(update={"model_timeout_seconds": arguments.model_timeout})
    rules = (
        "你是严格的电商RAG评测标注员，仅根据给定知识块和独立用户Query标注。"
        "不得使用缺失历史、实际订单、用户身份、商品详情或常识补全；含糊指代从严判不可回答。"
        "should_answer表示资料是否完整回答问题；不可回答时expected_sources为空、"
        "reference_answer为'根据现有知识库无法确定。'。"
        "expected_tool独立于可回答性：询问一般政策、规则、保修或使用方法需search_knowledge，"
        "即使知识库缺答案也需检索；问候、缺失指代、个人订单状态查询、执行购买/退款/取消等操作为null。"
        "本实验只允许知识工具，不以是否能从知识库找到答案决定该不该使用工具。"
        "allowed_categories表示检索参数允许使用的分类，可多个，按Query及当前证据标注，"
        "不要盲从原category；没有适合过滤类别时为空，工具省略category始终合法。"
        "保持id、query、category原值。只返回JSON数组，每项包含id/query/category/should_answer/"
        "expected_sources/reference_answer/expected_tool/allowed_categories/confidence/review_note。"
    )
    batches = []
    for start in range(0, len(original), arguments.batch_size):
        batch = []
        for item in original[start:start + arguments.batch_size]:
            if item["id"] not in completed:
                batch.append({"id": item["id"], "query": item["query"], "category": item["category"]})
        if not batch:
            continue
        batches.append(batch)

    def annotate_batch(batch):
        client = create_model_client(app_settings=annotation_settings, tools=[])
        payload = json.dumps(batch, ensure_ascii=False)
        proposed = invoke_json_array(client, rules, f"全部知识块：\n{context}\n问题：\n{payload}")
        validate_runtime_batch(batch, proposed, valid_sources)
        verified = invoke_json_array(
            client, rules + "你现在审核候选标注，直接修正错误，尤其检查证据充分性和工具是否必要。",
            f"全部知识块：\n{context}\n原问题：\n{payload}\n候选：\n{json.dumps(proposed, ensure_ascii=False)}",
        )
        validate_runtime_batch(batch, verified, valid_sources)
        return verified

    failures = []
    with ThreadPoolExecutor(max_workers=arguments.workers) as pool:
        futures = []
        for batch in batches:
            futures.append(pool.submit(annotate_batch, batch))
        for future in as_completed(futures):
            try:
                verified = future.result()
            except Exception as error:
                failures.append(str(error))
                print(f"标注批次失败：{error}", flush=True)
                continue
            for item in verified:
                item["review_status"] = "ai_verified"
                item["review_method"] = "two_pass_llm_current_knowledge"
                item["annotation_model"] = settings.model_name
                item["knowledge_sha256"] = knowledge_hash
                item["parameter_check"] = "semantic"
                completed[item["id"]] = item
            ordered = []
            for item in original:
                if item["id"] in completed:
                    ordered.append(completed[item["id"]])
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"已核验 {len(completed)}/100，知识块 {len(chunks)}", flush=True)
    if failures:
        raise RuntimeError(f"{len(failures)}个批次未完成，可使用相同命令续跑")


if __name__ == "__main__":
    main()
