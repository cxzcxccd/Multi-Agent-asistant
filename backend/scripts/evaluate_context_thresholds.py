"""Paired synthetic threshold experiment; no model calls or business writes."""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from statistics import mean

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.ai.context import context_message, derive_context
from app.ai.context_budget import ContextBudgetExceededError, count_tokens, prepare_context, serialized_request
from app.ai.context_prompts import resident_prompt
from app.ai.scenes import project_scenes, scene_sections
from app.ai.tools.catalog import search_products
from app.core.config import Settings
from scripts.evaluate_context_compression import cases


RATIOS = (0.5, 0.6, 0.7, 0.8, 0.9)


def requests():
    for name, history in cases().items():
        # Keep the original stress set, plus shorter versions where thresholds
        # can actually differ. All ratios see byte-identical inputs.
        for repetitions in (1, 5, 200):
            if name == "required_overflow" and repetitions != 200:
                continue
            adapted = []
            for message in history:
                if isinstance(message, HumanMessage) and str(message.id).startswith("filler-"):
                    message = message.model_copy(update={"content": "谢谢，我了解了。" * repetitions})
                elif isinstance(message, AIMessage) and str(message.content).startswith("好的，很高兴为你服务。"):
                    message = message.model_copy(update={"content": "好的，很高兴为你服务。" * repetitions})
                adapted.append(message)
            required, optional = scene_sections(project_scenes(adapted))
            memory = context_message(derive_context(adapted), required)
            pinned = str(memory.content)
            memory = memory.model_copy(update={
                "content": pinned + ("\n" + "\n".join(optional) if optional else ""),
                "additional_kwargs": {**memory.additional_kwargs,
                                      "context_parts": {"required": pinned, "optional": optional}},
            })
            yield f"{name}-repeat{repetitions}", name, pinned, [SystemMessage(content=resident_prompt()), memory, *adapted]


def run(output):
    rows = []
    base = Settings(_env_file=None, context_window_tokens=16384,
                    context_output_reserve_tokens=2048, context_protocol_reserve_tokens=1024)
    fixture = list(requests())
    for ratio in RATIOS:
        config = base.model_copy(update={"context_compress_ratio": ratio})
        for case_id, name, pinned, request in fixture:
            before = count_tokens(request, [search_products], config.context_token_encoding)
            row = {"case": case_id, "ratio": ratio, "before": before,
                   "request_sha256": hashlib.sha256(serialized_request(request, [search_products]).encode()).hexdigest()}
            try:
                bounded, report = prepare_context(request, [search_products], config)
                current_kept = any(m.id == "current" for m in bounded)
                facts_kept = any(pinned in str(m.content) for m in bounded)
                tool_chain_kept = all(any(m.type == old.type and getattr(m, "tool_call_id", None) == getattr(old, "tool_call_id", None)
                                         and (not isinstance(old, AIMessage) or m.tool_calls == old.tool_calls)
                                         for m in bounded)
                                      for old in request if old.type == "tool" or isinstance(old, AIMessage) and old.tool_calls)
                row.update(asdict(report), rejected=False, current_kept=current_kept,
                           facts_kept=facts_kept, tool_chain_kept=tool_chain_kept,
                           passed=name != "required_overflow" and current_kept and facts_kept and tool_chain_kept
                                  and report.estimated_after <= report.input_budget,
                           compression_triggered=before > report.trigger_budget)
            except ContextBudgetExceededError:
                row.update(rejected=True, passed=name == "required_overflow")
            rows.append(row)
    # Exercise exact > boundary independently of scene extraction.
    boundaries = []
    probe = [SystemMessage(content="固定规则"), HumanMessage(content="当前问题", id="current")]
    n = count_tokens(probe, [search_products], base.context_token_encoding)
    for trigger in (n - 1, n, n + 1):
        config = base.model_copy(update={"context_compress_ratio": (trigger + 0.25) / 13312})
        bounded, report = prepare_context(probe, [search_products], config)
        actual = len(bounded) > len(probe)
        boundaries.append({"before": n, "trigger": report.trigger_budget, "entered_compression": actual,
                           "passed": actual == (n > trigger)})
    summaries = []
    for ratio in RATIOS:
        group = [r for r in rows if r["ratio"] == ratio]
        sent = [r for r in group if not r["rejected"]]
        summaries.append({"ratio": ratio, "trigger": int(13312 * ratio), "cases": len(group),
                          "passed": sum(r["passed"] for r in group), "rejected": len(group) - len(sent),
                          "compressed": sum(r["compression_triggered"] for r in sent),
                          "mean_after": round(mean(r["estimated_after"] for r in sent), 2),
                          "mean_removed_messages": round(mean(r["removed_history_messages"] for r in sent), 2)})
    root = Path(__file__).resolve().parents[1]
    result = {"measurement": "local_bpe_proxy_not_provider_usage", "dataset": "paired-synthetic-threshold-v1",
              "input_budget": 13312, "encoding": base.context_token_encoding,
              "ratios": RATIOS, "fixture_repetitions": [1, 5, 200],
              "unique_requests": len(fixture), "comparisons": len(rows),
              "source_sha256": {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in
                                ("scripts/evaluate_context_thresholds.py", "scripts/evaluate_context_compression.py",
                                 "app/ai/context_budget.py", "app/ai/context.py", "app/ai/scenes.py")},
              "summaries": summaries, "boundaries": boundaries, "results": rows,
              "limitations": "Synthetic deterministic checks only; no answer quality, semantic equivalence, cost or optimal ratio claims."}
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 上下文压缩阈值对照", "", "同一输入配对测试；本地BPE计数，无在线模型调用。", "",
             "| 比例 | 触发点 | 检查通过 | 压缩请求数 | 拒绝数 | 已发送请求平均Token估算 | 平均删除消息数 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for s in summaries:
        lines.append(f"| {s['ratio']:.0%} | {s['trigger']} | {s['passed']}/{s['cases']} | {s['compressed']} | {s['rejected']} | {s['mean_after']} | {s['mean_removed_messages']} |")
    lines += ["", f"共{len(fixture)}个不同输入×{len(RATIOS)}种比例={len(rows)}次对照；另有3项精确边界检查。",
              "输入为原7类场景：6类分别使用1、5、200次寒暄短句重复，另1类为必要信息超限。保留40轮寒暄及原业务事实。这些是人为长度档位，不是实际客服流量。",
              "均值分母为各比例下未拒绝的18个输入；删除的是原始消息，相关场景事实另外保留。压缩请求数不是长期会话压缩频率。",
              "验证当前问题、必要场景正文、工具链、硬预算及预期超限拒绝；不验证LLM回答正确率或未提取的隐含事实。",
              "边界检查验证before>trigger时进入压缩，等于及低于阈值时不进入。",
              "结论：本实验只能比较预算和裁剪行为，不能证明80%最优；默认值保持不变。源代码及请求哈希见report.json。"]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"summaries": summaries, "boundaries": boundaries, "output": str(output.resolve())}, ensure_ascii=False))
    if not all(r["passed"] for r in rows + boundaries):
        raise SystemExit(1)


if __name__ == "__main__":
    run(Path("../docs/evaluations/context-thresholds-2026-10-14"))
