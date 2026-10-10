"""真实模型选择知识工具、执行检索和作答的受控实验，不代表完整 Supervisor。"""

import argparse
import hashlib
import json
import math
import time
from datetime import UTC, datetime
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from app.ai.model_client import create_model_client
from app.ai.tools.knowledge import KnowledgeSearchInput, search_knowledge, get_knowledge_tool_service
from app.core.config import settings
from app.db.base import Base
from app.modules.knowledge.embeddings import create_embedding_provider
from app.modules.knowledge.indexer import KnowledgeIndexer
from app.modules.knowledge.loader import load_knowledge_directory
from app.modules.knowledge.repository import KnowledgeRepository
from app.modules.knowledge.service import KnowledgeService
from app.modules.knowledge.vector_store import InMemoryKnowledgeVectorStore
from app.modules.knowledge.evaluation import ModelRagAnswerEngine, RagEvaluator
from app.modules.knowledge.schemas import KnowledgeSearchItem


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def configuration_fingerprint() -> str:
    """只存配置摘要，不记录地址或密钥；防止续跑混用不同模型与检索方案。"""
    values = {
        "provider": settings.model_provider,
        "model": settings.model_name,
        "base_url": settings.model_base_url,
        "temperature": settings.model_temperature,
        "timeout": settings.model_timeout_seconds,
        "embedding": settings.embedding_model,
        "vector_min": settings.knowledge_min_score,
        "bm25_min": settings.knowledge_bm25_min_score,
        "bm25_k1": settings.knowledge_bm25_k1,
        "bm25_b": settings.knowledge_bm25_b,
        "rrf": settings.knowledge_rrf_rank_constant,
        "hnsw_m": settings.milvus_hnsw_m,
        "hnsw_construction": settings.milvus_hnsw_ef_construction,
        "hnsw_ef": settings.milvus_hnsw_ef,
    }
    serialized = json.dumps(values, sort_keys=True).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def validate_parameters(arguments: dict, case: dict) -> bool:
    """检查结构、问题关键事实和分类；这是受控样本契约，不是通用语义评审。"""
    try:
        parsed = KnowledgeSearchInput.model_validate(arguments)
    except ValueError:
        return False
    if parsed.category is not None and parsed.category != case["category"]:
        if parsed.category not in case.get("allowed_categories", []):
            return False
    if case.get("parameter_check") == "semantic":
        if parsed.category is not None and parsed.category not in case["allowed_categories"]:
            return False
        return True
    for term in case["parameter_terms"]:
        if term in parsed.query:
            return True
    return False


def run_case(client, judge, case: dict) -> dict:
    messages = [
        SystemMessage(content=(
            "你是电商知识客服。政策、规则和使用说明必须查询知识库；"
            "问候不需要工具。工具返回资料不充分时说明无法确定，禁止编造。"
            "使用资料时在结论后标注[来源: source]。"
        )),
        HumanMessage(content=case["query"]),
    ]
    started = time.perf_counter()
    contexts = []
    usage_records = []
    calls = []
    retrieval_latencies = []
    answer = None
    error = None
    model_failure = None
    selection_correct = False
    parameters_correct = None
    try:
        # 最多四次模型调用，超出范围的工具不执行，防止无限循环。
        for _round in range(4):
            response = client.invoke(messages)
            usage = getattr(response, "usage_metadata", None)
            usage_records.append(dict(usage) if usage else None)
            messages.append(response)
            if response.invalid_tool_calls:
                raise ValueError("模型返回无法解析的工具调用参数")
            if not response.tool_calls:
                answer = ModelRagAnswerEngine._message_text(response.content)
                break
            for call in response.tool_calls:
                correct = call["name"] == "search_knowledge"
                arguments = call["args"]
                valid = correct and validate_parameters(arguments, case)
                calls.append({"name": call["name"], "args": arguments, "parameters_correct": valid})
                if not correct:
                    raise ValueError("模型选择了不允许执行的工具")
                # 语义契约不通过仍执行合法参数，以观察真实结果；结构不合法直接失败。
                KnowledgeSearchInput.model_validate(arguments)
                retrieval_started = time.perf_counter()
                payload = search_knowledge.invoke(arguments)
                retrieval_latencies.append((time.perf_counter() - retrieval_started) * 1000)
                tool_error = payload.get("error")
                if tool_error and tool_error.get("code") == "VECTOR_DATABASE_UNAVAILABLE":
                    raise RuntimeError("Milvus 暂不可用")
                for source in payload["sources"]:
                    contexts.append(KnowledgeSearchItem.model_validate(source))
                messages.append(ToolMessage(
                    content=json.dumps(payload, ensure_ascii=False),
                    tool_call_id=call["id"],
                ))
        if answer is None:
            raise RuntimeError("超过四次模型调用仍未完成回答")
    except Exception as exc:
        error = type(exc).__name__ + ": " + str(exc)
        cause = exc.__cause__
        status = getattr(cause, "status_code", None)
        if status is not None:
            model_failure = {"http_status": status}
            if status == 402:
                model_failure["reason"] = "provider_balance_insufficient"
    elapsed = (time.perf_counter() - started) * 1000
    if case["expected_tool"] is None:
        selection_correct = not calls
    else:
        selection_correct = bool(calls)
        for call in calls:
            if call["name"] != case["expected_tool"]:
                selection_correct = False
        if calls:
            parameters_correct = True
            for call in calls:
                if not call["parameters_correct"]:
                    parameters_correct = False
    if not usage_records:
        # 模型没有返回任何响应，不能把“没调用工具”算作正确选择。
        selection_correct = None
    quality = None
    evaluation_error = None
    parameter_review = None
    if calls and case.get("parameter_check") == "semantic":
        try:
            review_calls = []
            for call in calls:
                # 不把本地契约检查结果透露给语义评审，避免诱导结论。
                review_calls.append({"name": call["name"], "args": call["args"]})
            review_messages = [
                SystemMessage(content=(
                    "审核知识检索工具的query参数是否忠于用户原问题。允许简化、同义改写、补足明确语义；"
                    "不得增加未提供的订单编号、商品型号、价格、平台或其他具体事实，也不能丢失核心诉求。"
                    "逐项输出JSON对象：{\"valid\":[true,false],\"reason\":\"原因\"}，valid顺序与调用列表一致。"
                    "只审核query，不审核工具选择或检索结果，不能因为没有答案判参数错误。"
                )),
                HumanMessage(content=json.dumps({"query": case["query"], "calls": review_calls}, ensure_ascii=False)),
            ]
            review_response = judge.model_client.invoke(review_messages)
            parameter_review = ModelRagAnswerEngine._parse_json_object(str(review_response.content))
            flags = parameter_review["valid"]
            if len(flags) != len(calls):
                raise ValueError("参数评审数量不一致")
            for call, flag in zip(calls, flags, strict=True):
                if not isinstance(flag, bool):
                    raise ValueError("参数评审必须返回布尔值")
                call["parameters_correct"] = call["parameters_correct"] and flag
                if not call["parameters_correct"]:
                    parameters_correct = False
        except Exception as exc:
            evaluation_error = "parameter_review: " + type(exc).__name__ + ": " + str(exc)
            parameters_correct = None
    if answer is not None:
        try:
            quality = judge.judge(case["query"], case["reference_answer"], contexts, answer)
        except Exception as exc:
            evaluation_error = type(exc).__name__ + ": " + str(exc)
    task_passed = False
    if quality is not None:
        task_passed = (
            error is None and evaluation_error is None and selection_correct and parameters_correct is not False
            and RagEvaluator._answer_passed(quality)
        )
    source_ids = []
    for item in contexts:
        if item.source not in source_ids:
            source_ids.append(item.source)
    return {
        "id": case["id"], "query": case["query"], "calls": calls,
        "tool_selection_correct": selection_correct,
        "parameters_correct": parameters_correct, "task_passed": task_passed,
        "end_to_end_latency_ms": round(elapsed, 2),
        "retrieval_latencies_ms": retrieval_latencies,
        "usage": RagEvaluator._sum_usage(usage_records),
        "answer": answer, "sources": source_ids,
        "quality": quality.model_dump() if quality is not None else None,
        "parameter_review": parameter_review,
        "error": error, "evaluation_error": evaluation_error,
        "model_failure": model_failure,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=BACKEND_ROOT / "data/evaluation/rag_runtime_smoke.json")
    parser.add_argument("--output", type=Path, default=BACKEND_ROOT / "data/evaluation/results/rag-runtime")
    parser.add_argument("--in-memory", action="store_true", help="真实BGE、隔离临时正文库和内存余弦检索，不修改业务数据库")
    parser.add_argument("--input-price-per-million", type=float)
    parser.add_argument("--output-price-per-million", type=float)
    parser.add_argument("--cost-currency", default="CNY")
    parser.add_argument("--workers", type=int, default=1, help="并发样本数，延迟报告必须注明负载")
    parser.add_argument("--resume", action="store_true", help="保留已完成样本，重试运行失败或评审失败；失败历史不覆盖")
    arguments = parser.parse_args()
    if arguments.workers < 1 or arguments.workers > 8:
        parser.error("并发样本数须在1到8之间")
    prices = (arguments.input_price_per_million, arguments.output_price_per_million)
    if (prices[0] is None) != (prices[1] is None):
        parser.error("输入与输出单价必须同时提供")
    for price in prices:
        if price is not None and (not math.isfinite(price) or price < 0):
            parser.error("价格必须是有限的非负数")
    cases = json.loads(arguments.cases.read_text(encoding="utf-8"))
    if not cases:
        parser.error("评测集不能为空")
    for case in cases:
        if "expected_tool" not in case:
            parser.error("数据缺少工具标签；原JDDC测试集需先运行 scripts.prepare_jddc_runtime_test，不可直接复用旧来源标签")
    def evaluate_case(case):
        # 每个样本独立客户端与评审状态，避免并发共享usage_records。
        client = create_model_client(tools=[search_knowledge])
        judge = ModelRagAnswerEngine(create_model_client(tools=[]))
        result = run_case(client, judge, case)
        failure = result.get("model_failure")
        if failure and failure.get("http_status") == 402:
            provider_blocked.set()
        return result
    provider_blocked = Event()
    results = []
    arguments.output.mkdir(parents=True, exist_ok=True)
    detail_path = arguments.output / "runtime_results.jsonl"
    previous_results = {}
    if arguments.resume and detail_path.exists():
        report_path = arguments.output / "runtime_report.json"
        if not report_path.exists():
            parser.error("续跑需要原报告，用于核对数据与模型配置")
        previous_report = json.loads(report_path.read_text(encoding="utf-8"))
        if previous_report["cases_sha256"] != hashlib.sha256(arguments.cases.read_bytes()).hexdigest():
            parser.error("评测数据已变化，不能续跑")
        if previous_report["model"] != settings.model_name:
            parser.error("模型已变化，请使用新输出目录，不能混合成绩")
        if previous_report.get("workers", 1) != arguments.workers:
            parser.error("并发条件已变化，请使用新输出目录，不能混合延迟")
        if previous_report.get("configuration_fingerprint") != configuration_fingerprint():
            parser.error("模型或检索配置已变化，请使用新输出目录")
        # 先归档整个失败轮次，避免重试掩盖原始运行错误。
        archive = arguments.output / ("attempt-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f"))
        archive.mkdir()
        (archive / detail_path.name).write_bytes(detail_path.read_bytes())
        (archive / report_path.name).write_bytes(report_path.read_bytes())
        for line in detail_path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record["error"] is None and record["evaluation_error"] is None:
                previous_results[record["id"]] = record
    def evaluate_or_reuse(case):
        if case["id"] in previous_results:
            return previous_results[case["id"]]
        if provider_blocked.is_set():
            # 已知余额不足时不继续消耗重试；保留样本，恢复后仍可续跑。
            return {
                "id": case["id"], "query": case["query"], "calls": [],
                "tool_selection_correct": None, "parameters_correct": None,
                "task_passed": False, "end_to_end_latency_ms": 0,
                "retrieval_latencies_ms": [], "usage": None, "answer": None,
                "sources": [], "quality": None, "parameter_review": None,
                "error": "Skipped: provider balance insufficient", "evaluation_error": None,
                "model_failure": {"http_status": 402, "reason": "skipped_after_provider_balance_failure"},
            }
        return evaluate_case(case)
    with TemporaryDirectory() as temporary_directory:
        initialization_started = time.perf_counter()
        service_context = nullcontext()
        engine = None
        if arguments.in_memory:
            engine = create_engine("sqlite:///" + str(Path(temporary_directory) / "knowledge.db"))
            Base.metadata.create_all(engine)
            repository = KnowledgeRepository(sessionmaker(bind=engine, expire_on_commit=False))
            provider = create_embedding_provider()
            store = InMemoryKnowledgeVectorStore("rag_runtime")
            chunks = load_knowledge_directory(BACKEND_ROOT / "data/knowledge")
            KnowledgeIndexer(repository, provider, store).rebuild(chunks)
            service = KnowledgeService(repository, provider, store)
            # 只替换本次离线实验的存储入口，模型和工具执行均为真实调用。
            service_context = patch("app.ai.tools.knowledge.get_knowledge_tool_service", return_value=service)
        else:
            service = get_knowledge_tool_service()
        # 将索引同步与Embedding首次加载单独记录，不混入请求P95。
        service.embedding_provider.embed_query("知识检索预热")
        initialization_ms = (time.perf_counter() - initialization_started) * 1000
        try:
            with service_context, detail_path.open("w", encoding="utf-8") as output, ThreadPoolExecutor(max_workers=arguments.workers) as pool:
                for result in pool.map(evaluate_or_reuse, cases):
                    results.append(result)
                    output.write(json.dumps(result, ensure_ascii=False) + "\n")
                    output.flush()
                    print(f"{len(results)}/{len(cases)} {result['id']}: task={result['task_passed']}, error={result['error']}", flush=True)
        finally:
            if engine is not None:
                engine.dispose()
    parameter_cases = []
    latencies = []
    selection_count = 0
    selection_cases = 0
    success_count = 0
    usage_count = 0
    evaluation_failures = 0
    runtime_errors = 0
    skipped_cases = 0
    knowledge_required = 0
    answerable_cases = 0
    quality_scores = []
    total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for result in results:
        if result["error"] is not None:
            runtime_errors += 1
        if result["error"] == "Skipped: provider balance insufficient":
            skipped_cases += 1
        if result["tool_selection_correct"] is not None:
            selection_cases += 1
            selection_count += int(result["tool_selection_correct"])
        success_count += int(result["task_passed"])
        if result["parameters_correct"] is not None:
            parameter_cases.append(result["parameters_correct"])
        if result["error"] is None:
            latencies.append(result["end_to_end_latency_ms"])
        if result["usage"] is not None:
            usage_count += 1
            for key in total_usage:
                total_usage[key] += result["usage"].get(key, 0)
        if result["evaluation_error"] is not None:
            evaluation_failures += 1
        if result["quality"] is not None:
            quality_scores.append(result["quality"])
    for case in cases:
        if case["expected_tool"] == "search_knowledge":
            knowledge_required += 1
        if case.get("should_answer"):
            answerable_cases += 1
    quality_averages = {}
    for field in ("correctness", "faithfulness", "completeness", "citation_correctness"):
        values = []
        for quality in quality_scores:
            values.append(quality[field])
        quality_averages[field] = RagEvaluator._average(values) if values else None
    report = {
        "scope": "单个知识工具的受控真实模型实验，不是完整多Agent或JDDC500评测",
        "model": settings.model_name, "cases": len(results),
        "workers": arguments.workers,
        "configuration_fingerprint": configuration_fingerprint(),
        "reused_cases": len(previous_results),
        "initialization_ms": round(initialization_ms, 2),
        "expected_knowledge_tool_cases": knowledge_required,
        "answerable_cases": answerable_cases,
        "unanswerable_cases": len(cases) - answerable_cases,
        "evaluation_failure_cases": evaluation_failures,
        "runtime_error_cases": runtime_errors,
        "skipped_cases": skipped_cases,
        "quality_cases": len(quality_scores),
        "quality_averages": quality_averages,
        "created_at": datetime.now(UTC).isoformat(),
        "cases_sha256": hashlib.sha256(arguments.cases.read_bytes()).hexdigest(),
        "embedding_model": settings.embedding_model,
        "strategy": "hybrid: BM25 + BGE + RRF",
        "vector_min_score": settings.knowledge_min_score,
        "bm25_min_score": settings.knowledge_bm25_min_score,
        "rrf_rank_constant": settings.knowledge_rrf_rank_constant,
        "hnsw_m": settings.milvus_hnsw_m,
        "hnsw_ef_construction": settings.milvus_hnsw_ef_construction,
        "hnsw_ef": settings.milvus_hnsw_ef,
        "retrieval_backend": "真实BGE+内存余弦" if arguments.in_memory else "真实BGE+Milvus",
        "tool_selection_correct": selection_count,
        "tool_selection_cases": selection_cases,
        "tool_selection_accuracy": selection_count / selection_cases if selection_cases else None,
        "parameter_valid_cases": sum(parameter_cases), "parameter_cases": len(parameter_cases),
        "parameter_accuracy": sum(parameter_cases) / len(parameter_cases) if parameter_cases else None,
        "parameter_definition": "结构合法、类别在允许集合内、query保持用户事实与诉求（semantic样本由模型核验；旧smoke样本检查关键事实词）；逐任务所有调用通过",
        "task_successes": success_count, "task_completion_rate": success_count / len(results),
        "task_definition": "调用无错误、工具选择正确、参数契约通过、模型评审三项分数均>=0.7；含正确拒答与问候",
        "end_to_end_p95_ms": RagEvaluator._percentile(latencies, 0.95) if latencies else None,
        "latency_cases": len(latencies), "latency_definition": "含模型选择工具、实际检索和最终回答，不含评审；只统计运行成功请求",
        "usage_cases": usage_count, "generation_usage": total_usage,
        "estimated_total_cost": None, "average_request_cost": None,
        "input_price_per_million": prices[0], "output_price_per_million": prices[1],
        "cost_currency": arguments.cost_currency if prices[0] is not None else None,
        "cost_note": "需完整用量和用户提供单价；仅为在线路径模型费用估算，不含评审、本地计算、缓存折扣或完整会话成本",
        "judge": "与生成同配置模型，独立调用但存在自评偏差",
    }
    if prices[0] is not None and usage_count == len(results):
        cost = (total_usage["input_tokens"] * prices[0] + total_usage["output_tokens"] * prices[1]) / 1_000_000
        report["estimated_total_cost"] = cost
        report["average_request_cost"] = cost / len(results)
    (arguments.output / "runtime_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
