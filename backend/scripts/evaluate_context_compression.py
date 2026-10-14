"""Reproducible synthetic stress cases; no paid model or business writes."""

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.ai.context import context_message, derive_context
from app.ai.context_budget import prepare_context, count_tokens, ContextBudgetExceededError, serialized_request
from app.ai.context_prompts import resident_prompt
from app.ai.scenes import project_scenes, scene_sections
from app.ai.tools.catalog import search_products
from app.core.config import Settings


def user(text, source):
    return HumanMessage(content=text, id=source)


def filler():
    # Deliberately redundant synthetic turns, not representative real traffic.
    result = []
    for index in range(40):
        result.extend([user("谢谢，我了解了。" * 200, f"filler-{index}"),
                       AIMessage(content="好的，很高兴为你服务。" * 200)])
    return result


def cases():
    policy = "仅适用对应渠道，未激活不单独代表符合退货条件。" * 50
    return {
        "duplicate": [user("耳机未激活能退吗", "initial"),
                      *[user("耳机未激活能退吗", f"repeat-{i}") for i in range(20)],
                      *filler(), user("它能退吗", "current")],
        "separate_orders": [user("订单10002想退货", "order-a"), user("订单10003查物流", "order-b"),
                            *filler(), user("这单到哪了", "current")],
        "correction": [user("订单10002耳机未激活想退货", "old"),
                       user("不是订单10002，是10003，我要退货", "correction"),
                       *filler(), user("这单怎么处理", "current")],
        "attempt_and_switch": [user("耳机左边没声音", "fault"),
                               user("已经重置过，左边仍没声音", "attempt"),
                               user("65W USB-C充电器兼容吗", "charger"),
                               *filler(), user("回到耳机没声音的问题", "current")],
        "archive": [user("耳机能退吗", "old"), user("这个问题解决了", "resolved"),
                    user("手机未激活能退吗", "phone"), *filler(), user("它能退吗", "current")],
        "tool_evidence": [*filler(), user("耳机退货规则", "current"),
                          AIMessage(content="", tool_calls=[{"id": "knowledge", "name": "search_knowledge",
                                                             "args": {"query": "耳机退货"}}]),
                          ToolMessage(content=json.dumps({"sources": [{"content": policy, "source": "fixture#policy"}],
                                                          "answer_context": policy}), tool_call_id="knowledge")],
        "required_overflow": [user("订单10002我要退货，原因：" + "必须保留的申请原因。" * 5000, "current")],
    }


def run(output: Path):
    config = Settings(_env_file=None, context_window_tokens=4096,
                      context_output_reserve_tokens=512, context_protocol_reserve_tokens=256)
    results = []
    for name, history in cases().items():
        snapshot = project_scenes(history)
        required, optional = scene_sections(snapshot)
        memory = context_message(derive_context(history), required)
        memory = memory.model_copy(update={
            "content": str(memory.content) + ("\n" + "\n".join(optional) if optional else ""),
            "additional_kwargs": {**memory.additional_kwargs,
                "context_parts": {"required": str(memory.content), "optional": optional}},
        })
        request = [SystemMessage(content=resident_prompt()), memory, *history]
        result = {"case": name, "history_messages": len(history),
                  "request_sha256": hashlib.sha256(serialized_request(request, [search_products]).encode()).hexdigest(),
                  "scene_count": len(snapshot.scenes),
                  "open_scene_count": sum(scene.status == "open" for scene in snapshot.scenes),
                  "scenes": snapshot.model_dump(),
                  "estimated_before": count_tokens(request, [search_products], config.context_token_encoding)}
        try:
            bounded, report = prepare_context(request, [search_products], config)
            result.update(asdict(report))
            result["output_request"] = json.loads(serialized_request(bounded, [search_products]))
            result["passed"] = (name != "required_overflow" and report.estimated_after <= report.input_budget
                                and any(message.id == "current" for message in bounded)
                                and str(memory.content).split("\n已归档场景")[0] in str(bounded[1].content))
            result["outcome"] = "bounded_request"
        except ContextBudgetExceededError:
            result.update(passed=name == "required_overflow", outcome="required_overflow_rejected")
        results.append(result)
    root = Path(__file__).resolve().parents[1]
    source_paths = ["app/ai/scenes.py", "app/ai/context_budget.py", "app/ai/context.py",
                    "scripts/evaluate_context_compression.py"]
    report = {"dataset": "synthetic-context-stress-v1", "measurement": "local_bpe_proxy_not_provider_usage",
              "encoding": config.context_token_encoding,
              "budget_configuration": {key: getattr(config, key) for key in (
                  "context_window_tokens", "context_output_reserve_tokens", "context_protocol_reserve_tokens", "context_compress_ratio")},
              "fixture_parameters": {"filler_turns": 40, "filler_phrase_repetitions": 200,
                                     "duplicate_questions": 20, "policy_repetitions": 50,
                                     "overflow_reason_repetitions": 5000},
              "source_sha256": {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in source_paths},
              "passed": sum(result["passed"] for result in results), "cases": len(results), "results": results}
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 上下文压缩固定实验", "", "合成压力案例；本地BPE估算，不是服务端Token或真实任务成功率。", "",
             "| 案例 | 输入消息数 | 压缩前估算 | 压缩后估算 | 结果 |", "| --- | ---: | ---: | ---: | --- |"]
    for result in results:
        lines.append(f"| {result['case']} | {result['history_messages']} | {result['estimated_before']} | {result.get('estimated_after', '未发送')} | {result['outcome']} |")
    lines += ["", f"检查通过：{report['passed']}/{report['cases']}。包含必要信息超限拒绝案例，不代表模型回答成功。",
              "", "所有计数、预算、输入摘要哈希、代码哈希、完整场景和压缩后请求见report.json；输入由脚本可重建。"]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "cases": report["cases"], "report": str(output.resolve()),
                      "measurements": [{key: result.get(key) for key in ("case", "estimated_before", "estimated_after", "outcome")} for result in results]}, ensure_ascii=False))
    if report["passed"] != report["cases"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("../docs/evaluations/context-compression-2026-10-14"))
    run(parser.parse_args().output)
