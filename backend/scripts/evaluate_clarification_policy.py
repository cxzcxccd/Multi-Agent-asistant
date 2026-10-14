"""评估结构化追问与知识工具门禁，保留原问题和标签，不冒充完整Agent评测。"""

import argparse
import asyncio
import hashlib
import inspect
import json
import shutil
from datetime import UTC, datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter

from langchain_core.messages import AIMessage, HumanMessage

from app.ai.clarification import (
    QueryUnderstanding, QueryUnderstandingService, UNDERSTANDING_PROMPT,
    clarification_question, find_missing_fields,
)
from app.ai.model_client import ModelClient, create_model_client
from app.ai.tools.knowledge import search_knowledge
from app.core.config import settings
from app.modules.knowledge.evaluation import ModelRagAnswerEngine
from scripts.evaluate_rag_runtime import run_case


class GuardedKnowledgeClient:
    """同一结构化理解与槽位规则约束工具；不读取expected_tool或参考答案。"""

    def __init__(self) -> None:
        self._raw_model = create_model_client(tools=[]).model
        self._understanding = QueryUnderstandingService(self._raw_model)
        self._client = None
        self.understanding = None
        self.missing_fields = []
        self.gate_usage = None
        self.gate_latency_ms = None

    def invoke(self, messages):
        if self.understanding is None:
            user_query = None
            for message in reversed(messages):
                if isinstance(message, HumanMessage):
                    user_query = message
                    break
            if user_query is None:
                raise ValueError("缺少用户Query")
            started = perf_counter()
            self.understanding = asyncio.run(self._understanding.analyze([user_query]))
            self.gate_latency_ms = (perf_counter() - started) * 1000
            self.gate_usage = self._understanding.last_usage
            self.missing_fields = find_missing_fields(self.understanding.tasks)
            if self.missing_fields:
                return AIMessage(content=clarification_question(self.missing_fields, self.understanding.tasks))
            policy_required = False
            for task in self.understanding.tasks:
                if task.operation in {"policy", "usage_help", "service_info"}:
                    policy_required = True
            selected_tools = [search_knowledge] if policy_required else []
            self._client = ModelClient(self._raw_model, tools=selected_tools)
        if self._client is None:
            raise ValueError("澄清任务不得继续执行工具")
        response = self._client.invoke(messages)
        if not self._client.tools and response.tool_calls:
            raise ValueError("模型返回门禁不允许的知识工具调用")
        return response


def policy_fingerprint() -> str:
    """只对本实验实际调用的理解/槽位/门禁逻辑和模型配置计算指纹。"""

    payload = {
        "prompt": UNDERSTANDING_PROMPT, "schema": QueryUnderstanding.model_json_schema(),
        "extractor": inspect.getsource(QueryUnderstandingService),
        "missing_fields": inspect.getsource(find_missing_fields),
        "question": inspect.getsource(clarification_question),
        "gate_client": inspect.getsource(GuardedKnowledgeClient),
        "model": settings.model_name, "provider": settings.model_provider,
        "base_url": settings.model_base_url, "temperature": settings.model_temperature,
        "timeout": settings.model_timeout_seconds, "retries": settings.model_max_retries,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--resume", action="store_true", help="仅重试运行或评审失败，保留已完成低分结果")
    arguments = parser.parse_args()
    if not 1 <= arguments.workers <= 8:
        parser.error("workers必须在1到8之间")
    cases = json.loads(arguments.cases.read_text(encoding="utf-8"))
    if not cases:
        parser.error("评测集不能为空")
    arguments.output.mkdir(parents=True, exist_ok=True)
    previous = {}
    cases_hash = hashlib.sha256(arguments.cases.read_bytes()).hexdigest()
    policy_hash = policy_fingerprint()
    if (arguments.output / "results.jsonl").exists():
        if not arguments.resume:
            parser.error("输出目录已有实验记录，请使用新目录，不能覆盖失败记录")
        report_path = arguments.output / "report.json"
        if not report_path.exists():
            parser.error("上一轮尚未生成完整报告，不能覆盖正在运行的实验")
        old_report = json.loads(report_path.read_text(encoding="utf-8"))
        if old_report["cases_sha256"] != cases_hash or old_report["workers"] != arguments.workers:
            parser.error("数据或并发条件已变化，请使用新目录")
        if old_report.get("policy_sha256") != policy_hash or old_report.get("model") != settings.model_name:
            parser.error("门禁代码或模型已变化，请使用新目录重跑，不能混合成绩")
        archive = arguments.output / ("attempt-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f"))
        archive.mkdir()
        shutil.copy2(report_path, archive / "report.json")
        shutil.copy2(arguments.output / "results.jsonl", archive / "results.jsonl")
        for line in (arguments.output / "results.jsonl").read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record["error"] is None and record["evaluation_error"] is None:
                previous[record["id"]] = record

    def evaluate(case):
        if case["id"] in previous:
            return previous[case["id"]]
        client = GuardedKnowledgeClient()
        judge = ModelRagAnswerEngine(create_model_client(tools=[]))
        result = run_case(client, judge, case)
        result["understanding"] = client.understanding.model_dump(mode="json") if client.understanding else None
        result["missing_fields"] = client.missing_fields
        result["gate_usage"] = client.gate_usage
        result["gate_latency_ms"] = client.gate_latency_ms
        return result

    results = []
    with (arguments.output / "results.jsonl").open("w", encoding="utf-8") as output:
        with ThreadPoolExecutor(max_workers=arguments.workers) as pool:
            for result in pool.map(evaluate, cases):
                results.append(result)
                output.write(json.dumps(result, ensure_ascii=False) + "\n")
                output.flush()
                print(f"{len(results)}/{len(cases)} {result['id']} correct={result['tool_selection_correct']} error={result['error']}", flush=True)
    correct = 0
    errors = 0
    clarified = 0
    evaluation_errors = 0
    for result in results:
        if result["error"] is not None:
            errors += 1
        elif result["tool_selection_correct"] is True:
            correct += 1
        if result["missing_fields"]:
            clarified += 1
        if result["evaluation_error"] is not None:
            evaluation_errors += 1
    report = {
        "scope": "知识工具是否调用的系统门禁实验，非模型自主选择准确率或完整多Agent任务完成率",
        "cases": len(cases), "correct": correct, "accuracy": correct / len(cases),
        "target": 0.95, "target_reached": correct / len(cases) >= 0.95,
        "runtime_errors": errors, "evaluation_errors": evaluation_errors, "clarification_cases": clarified,
        "cases_sha256": cases_hash, "reused_cases": len(previous),
        "policy_sha256": policy_hash, "model": settings.model_name,
        "created_at": datetime.now(UTC).isoformat(),
        "workers": arguments.workers,
        "note": "固定标签；错误计未通过。曾查看此测试集失败案例，因此是回归集，不能宣称独立盲测。",
        "usage_note": "门禁用量单独保存在gate_usage；澄清不调用回答模型，run_case的usage可能为null，不代表零总费用。",
    }
    (arguments.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
