"""Local BPE proxy budgets; never claim these estimates are provider usage."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import tiktoken
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage, HumanMessage, SystemMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.core.config import Settings, settings
from app.core.tracing import emit
from app.ai.context import CONTEXT_KEY
from app.ai.context import context_message, derive_context
from app.ai.scenes import project_scenes, scene_sections


class ContextBudgetExceededError(RuntimeError):
    """Required facts do not fit; do not silently cut user requests or evidence."""


@lru_cache
def encoding(name: str):
    return tiktoken.get_encoding(name)


def tool_schema(tool: Any) -> dict:
    return convert_to_openai_tool(tool)


def serialized_request(messages: Sequence[BaseMessage], tools: Sequence[Any] = ()) -> str:
    payload = []
    for message in messages:
        item = {"role": message.type, "content": message.content}
        if isinstance(message, AIMessage) and message.tool_calls:
            item["tool_calls"] = message.tool_calls
        if isinstance(message, ToolMessage):
            item.update(tool_call_id=message.tool_call_id, status=message.status)
        if message.name:
            item["name"] = message.name
        payload.append(item)
    return json.dumps({"messages": payload, "tools": [tool_schema(tool) for tool in tools]},
                      ensure_ascii=False, separators=(",", ":"))


def count_tokens(messages: Sequence[BaseMessage], tools: Sequence[Any] = (),
                 encoding_name: str | None = None) -> int:
    return len(encoding(encoding_name or settings.context_token_encoding).encode(
        serialized_request(messages, tools), disallowed_special=(),
    ))


def _deduplicate_payload(value: Any) -> Any:
    if isinstance(value, list):
        return [_deduplicate_payload(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _deduplicate_payload(item) for key, item in value.items()}
    sources = result.get("sources")
    if isinstance(sources, list) and sources and all(isinstance(item, dict) for item in sources):
        joined = "\n\n".join(str(item.get("content", "")) for item in sources)
        if result.get("answer_context") == joined:
            result.pop("answer_context")
            result["context_note"] = "正文完整保存在sources中，移除了重复的answer_context。"
    return result


def compact_evidence(value: Any) -> Any:
    return _deduplicate_payload(value)


@dataclass(frozen=True)
class BudgetReport:
    estimated_before: int
    estimated_after: int
    input_budget: int
    trigger_budget: int
    encoding: str
    removed_history_messages: int
    removed_optional_sections: int
    compacted_tool_messages: int


def prepare_context(messages: Sequence[BaseMessage], tools: Sequence[Any] = (),
                    config: Settings | None = None) -> tuple[list[BaseMessage], BudgetReport]:
    config = config or settings
    budget = config.context_window_tokens - config.context_output_reserve_tokens - config.context_protocol_reserve_tokens
    if budget <= 0:
        raise ContextBudgetExceededError("上下文配置无可用输入预算，请调整窗口或预留值")
    trigger = int(budget * config.context_compress_ratio)
    selected = list(messages)
    before = count_tokens(selected, tools, config.context_token_encoding)
    removed_history = removed_sections = compacted_tools = 0
    if before > trigger:
        if not any(CONTEXT_KEY in message.additional_kwargs for message in selected):
            required, optional = scene_sections(project_scenes(selected))
            memory = context_message(derive_context(selected), required)
            pinned = str(memory.content)
            memory = memory.model_copy(update={"additional_kwargs": {**memory.additional_kwargs,
                "context_parts": {"required": pinned, "optional": optional}}})
            selected.insert(0, memory)
        # Only eliminate byte-identical duplication, not policy conditions or fields.
        for index, message in enumerate(selected):
            if isinstance(message, ToolMessage):
                try:
                    value = json.loads(str(message.content))
                except (ValueError, TypeError):
                    continue
                compact = _deduplicate_payload(value)
                if compact != value:
                    selected[index] = message.model_copy(update={"content": json.dumps(compact, ensure_ascii=False, separators=(",", ":"))})
                    compacted_tools += 1
        # Optional archived scenes, preferences and retrieved history are separate
        # from the pinned current objects and all unresolved requests.
        for index, message in enumerate(selected):
            parts = message.additional_kwargs.get("context_parts")
            if not parts:
                continue
            optional = list(parts.get("optional", []))
            while optional and count_tokens(selected, tools, config.context_token_encoding) > trigger:
                optional.pop()
                removed_sections += 1
                content = parts["required"] + ("\n" + "\n".join(optional) if optional else "")
                selected[index] = message.model_copy(update={"content": content,
                    "additional_kwargs": {**message.additional_kwargs,
                        "context_parts": {"required": parts["required"], "optional": optional}}})
        # Remove old conversational turns as complete groups. The latest user
        # message and its complete tool-call/result chain are never orphaned.
        while count_tokens(selected, tools, config.context_token_encoding) > trigger:
            humans = [i for i, message in enumerate(selected) if isinstance(message, HumanMessage)]
            if len(humans) < 2:
                break
            removable = []
            for start, end in zip(humans, humans[1:]):
                removable = [i for i in range(start, end)
                             if not isinstance(selected[i], SystemMessage)
                             and CONTEXT_KEY not in selected[i].additional_kwargs
                             and not selected[i].additional_kwargs.get("context_required")]
                if removable:
                    break
            if not removable:
                break
            removed_history += len(removable)
            selected = [message for i, message in enumerate(selected) if i not in removable]
    after = count_tokens(selected, tools, config.context_token_encoding)
    report = BudgetReport(before, after, budget, trigger, config.context_token_encoding,
                          removed_history, removed_sections, compacted_tools)
    emit("context.budget", **report.__dict__, measurement="local_bpe_proxy",
         output_reserve=config.context_output_reserve_tokens,
         protocol_reserve=config.context_protocol_reserve_tokens)
    if after > budget:
        emit("context.budget.exceeded", estimated_tokens=after, input_budget=budget)
        raise ContextBudgetExceededError("必要诉求、工具定义或事实证据超过上下文预算，请缩小任务或调整配置")
    return selected, report
