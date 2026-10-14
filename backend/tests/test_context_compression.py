"""Compression invariants, with deterministic local tokenizer measurements."""

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.ai.context import context_message, derive_context
from app.ai.context import read_context, resolve_reference
from app.ai.context_budget import prepare_context, count_tokens, ContextBudgetExceededError
from app.ai.scenes import project_scenes, scene_sections
from app.ai.model_client import ModelClient
from app.ai.tools.catalog import search_products
from app.core.config import Settings
from tests.test_memory import make_memory, make_conversation
from tests.test_model_client import FakeBoundModel


def messages(*texts):
    return [HumanMessage(content=text, id=f"source-{index}") for index, text in enumerate(texts)]


def config(**changes):
    return Settings(_env_file=None, context_window_tokens=4096,
                    context_output_reserve_tokens=512, context_protocol_reserve_tokens=256,
                    **changes)


def pinned(history):
    snapshot = project_scenes(history)
    required, optional = scene_sections(snapshot)
    memory = context_message(derive_context(history), required)
    return memory.model_copy(update={"content": str(memory.content) + "\n" + "\n".join(optional),
        "additional_kwargs": {**memory.additional_kwargs,
            "context_parts": {"required": str(memory.content), "optional": optional}}})


def test_same_object_intent_and_conditions_aggregate_but_orders_do_not():
    snapshot = project_scenes(messages("订单10002耳机未激活，能退吗", "这单能退吗", "订单10003耳机未激活，能退吗"))
    assert len(snapshot.scenes) == 2
    assert snapshot.scenes[0].occurrences == 2
    assert snapshot.scenes[0].object_key == "order:10002"
    assert snapshot.scenes[1].object_key == "order:10003"
    assert all(scene.status == "open" for scene in snapshot.scenes)


def test_changed_conditions_keep_correction_and_provenance():
    snapshot = project_scenes(messages("耳机未激活能退吗", "耳机已经激活能退吗"))
    assert len(snapshot.scenes) == 2
    assert snapshot.scenes[0].status == "superseded"
    assert snapshot.scenes[1].conditions == ["已激活"]
    assert snapshot.scenes[1].supersedes == snapshot.scenes[0].id
    assert snapshot.scenes[1].condition_sources["已激活"] == "source-1"


def test_attempts_and_negative_results_survive_scene_switch():
    snapshot = project_scenes(messages("耳机左边没声音", "已经重置过，左边仍没声音", "充电器兼容吗", "回到耳机没声音的问题"))
    earphones = [scene for scene in snapshot.scenes if scene.object_key == "product:耳机" and scene.status == "open"]
    assert len(earphones) == 1
    assert "已经重置过，左边仍没声音" in earphones[0].attempts
    assert earphones[0].attempt_sources[earphones[0].attempts[0]] == "source-1"
    assert earphones[0].conditions == ["左边", "没声音"]


def test_user_resolution_archives_but_assistant_claim_is_not_authority():
    history = messages("订单10002我要退货")
    history += [AIMessage(content="退款已经批准到账")]
    assert project_scenes(history).scenes[0].status == "open"
    snapshot = project_scenes([*history, HumanMessage(content="这个问题解决了", id="resolved")])
    assert snapshot.scenes[0].status == "user_resolved"
    assert "到账" not in snapshot.scenes[0].goal
    assert project_scenes([*history, HumanMessage(content="不是这个问题解决了，是耳机没声音")]).scenes[0].status == "open"


def test_compound_requests_are_both_kept():
    snapshot = project_scenes(messages("订单10002想退货，顺便查物流"))
    assert {scene.operation for scene in snapshot.scenes} == {"return_apply", "logistics"}
    assert all(scene.object_key == "order:10002" for scene in snapshot.scenes)


def test_scene_cache_restarts_invalidates_and_excludes_provisional_turn(tmp_path):
    conversation = make_conversation()
    conversation.messages[0].content = "耳机未激活能退吗"
    memory = make_memory(tmp_path, [conversation])
    saved = memory.scenes(conversation)
    recreated = type(memory)(memory.session_factory, lambda buyer: [conversation])
    assert recreated.scenes(conversation) == saved
    memory.build_context(conversation, "手机能退吗", messages("耳机未激活能退吗", "手机能退吗"))
    assert recreated.scenes(conversation) == saved
    conversation.messages[0].content = "耳机已经激活能退吗"
    assert recreated.scenes(conversation).scenes[0].conditions == ["已激活"]


def test_large_history_is_reduced_and_open_request_stays():
    history = messages("订单10002耳机未激活，我要退货")
    for index in range(40):
        history += [HumanMessage(content="谢谢" * 200, id=f"filler-{index}"), AIMessage(content="不客气" * 200)]
    history += [HumanMessage(content="这单怎么处理", id="current")]
    source = [SystemMessage(content="常驻规则：不得绕过确认审批"), pinned(history), *history]
    bounded, report = prepare_context(source, [search_products], config())
    assert report.estimated_after <= report.input_budget
    assert report.estimated_before > report.estimated_after
    assert report.removed_history_messages > 0
    assert bounded[-1].id == "current"
    assert "未激活" in str(bounded[1].content) and "申请退货" in str(bounded[1].content)
    assert bounded[0].content == source[0].content
    assert count_tokens(bounded, [search_products], "cl100k_base") == report.estimated_after


def test_live_tool_chain_and_full_policy_conditions_stay():
    policy = "未激活不单独代表可以退货。仅适用对应渠道，请核验条件。"
    history = [*messages("谢谢" * 3000, "查耳机退货规则"),
        AIMessage(content="", tool_calls=[{"id": "call", "name": "search_knowledge", "args": {"query": "耳机退货"}}]),
        ToolMessage(content=json.dumps({"sources": [{"content": policy}], "answer_context": policy}), tool_call_id="call")]
    bounded, report = prepare_context(history, config=config())
    assert report.compacted_tool_messages == 1
    calls = [message for message in bounded if isinstance(message, AIMessage) and message.tool_calls]
    tools = [message for message in bounded if isinstance(message, ToolMessage)]
    assert calls[0].tool_calls[0]["id"] == tools[0].tool_call_id
    assert json.loads(str(tools[0].content))["sources"][0]["content"] == policy
    assert history[-1].content != tools[0].content  # No mutation of original evidence.


def test_required_overflow_stops_before_model_is_called():
    fake = FakeBoundModel()
    client = ModelClient(fake, tools=[], app_settings=config())
    with pytest.raises(ContextBudgetExceededError):
        client.invoke([SystemMessage(content="不能绕过审批" * 5000), HumanMessage(content="提交申请")])
    assert fake.calls == []


@pytest.mark.parametrize("mode", ["invoke", "ainvoke", "stream", "astream"])
def test_each_model_entrypoint_applies_budget(mode):
    fake = FakeBoundModel()
    client = ModelClient(fake, tools=[], app_settings=config())
    history = [*messages("谢谢" * 5000, "你好")]
    if mode == "invoke":
        client.invoke(history)
    elif mode == "ainvoke":
        asyncio.run(client.ainvoke(history))
    elif mode == "stream":
        list(client.stream(history))
    else:
        async def consume():
            return [chunk async for chunk in client.astream(history)]
        asyncio.run(consume())
    assert count_tokens(fake.calls[0][1], [], "cl100k_base") <= 4096 - 512 - 256


def test_tool_schema_cost_can_exceed_budget_without_any_large_chat():
    assert count_tokens(messages("你好"), [search_products]) > count_tokens(messages("你好"))
    with pytest.raises(ContextBudgetExceededError):
        prepare_context(messages("你好"), [search_products], Settings(_env_file=None,
            context_window_tokens=1024, context_output_reserve_tokens=900,
            context_protocol_reserve_tokens=100))


def test_explicit_constraints_and_negation_survive_pressure():
    history = messages("推荐耳机，预算200元。不要入耳式。用于会议")
    history += messages("谢谢" * 5000, "继续推荐它")
    bounded, _report = prepare_context([pinned(history), *history], config=config())
    memory = next(message for message in bounded if "未完成诉求" in str(message.content))
    for fact in ("不要入耳式", "用于会议", "200元"):
        assert fact in str(memory.content)


def test_returning_to_scene_restores_activation_for_hyde(tmp_path):
    conversation = make_conversation()
    memory = make_memory(tmp_path, [conversation])
    history = messages("耳机未激活能退吗", "充电器兼容吗", "回到耳机退货的问题", "那拆封了呢")
    assembled = memory.build_context(conversation, "那拆封了呢", history)
    expanded, question = resolve_reference("那拆封了呢", read_context(assembled))
    assert question is None and "未激活" in expanded and "耳机" in expanded
    assert read_context(assembled).activation.source_message_id == "source-0"


def test_optional_archives_removed_before_open_request():
    history = messages("耳机能退吗", "这个问题解决了", "手机未激活能退吗", "它能退吗")
    memory = pinned(history)
    parts = dict(memory.additional_kwargs["context_parts"])
    parts["optional"] += ["无关旧资料" * 4000]
    memory = memory.model_copy(update={"content": parts["required"] + "\n" + "\n".join(parts["optional"]),
                                      "additional_kwargs": {**memory.additional_kwargs, "context_parts": parts}})
    bounded, report = prepare_context([memory, history[-1]], config=config())
    assert report.removed_optional_sections >= 1
    assert "手机" in str(bounded[0].content) and "未激活" in str(bounded[0].content)


def test_platform_scope_is_not_merged_as_the_same_policy():
    snapshot = project_scenes(messages("京东耳机未激活能退吗", "淘宝耳机未激活能退吗"))
    assert len(snapshot.scenes) == 2
    assert "京东" in snapshot.scenes[0].conditions
    assert "淘宝" in snapshot.scenes[1].conditions and "京东" not in snapshot.scenes[1].conditions


def test_pending_original_request_is_pinned_while_other_history_is_removed():
    pending = HumanMessage(content="原始申请：订单10002退货", additional_kwargs={"context_required": True})
    history = [pending, *messages("谢谢" * 5000, "继续处理")]
    bounded, report = prepare_context(history, config=config())
    assert pending in bounded and bounded[-1].content == "继续处理"
    assert report.removed_history_messages > 0


def test_shortened_multiple_order_reference_remains_ambiguous():
    state = derive_context(messages("订单10002和10003都查一下"))
    assert state.order.value is None and state.order.candidates == ["10002", "10003"]
    assert resolve_reference("这单到哪了", state)[1] is not None


def test_suggested_solution_is_saved_but_not_marked_as_attempted_or_completed():
    snapshot = project_scenes([
        HumanMessage(content="耳机左边没声音", id="question"),
        AIMessage(content="可以尝试重新配对。退款已经批准到账。", id="proposal"),
    ])
    scene = snapshot.scenes[0]
    assert scene.suggestions == ["可以尝试重新配对"]
    assert scene.suggestion_sources[scene.suggestions[0]] == "proposal"
    assert scene.attempts == [] and scene.status == "open"


def test_domain_budget_failure_propagates_without_model_or_synthesis_call(monkeypatch):
    from app.ai.multi_agent.runtime import MultiAgentRuntime
    from app.ai.multi_agent.schemas import AgentName
    from app.ai.query_preprocessor import IntentName
    from app.core.config import settings
    from tests.test_multi_agent_runtime import FixedPreprocessor

    monkeypatch.setattr(settings, "context_window_tokens", 4096)
    monkeypatch.setattr(settings, "context_output_reserve_tokens", 512)
    monkeypatch.setattr(settings, "context_protocol_reserve_tokens", 256)

    class Forbidden:
        async def ainvoke(self, messages, config=None):
            raise AssertionError("No model call for an oversized required request")

    client = Forbidden()
    runtime = MultiAgentRuntime(client, query_preprocessor=FixedPreprocessor(IntentName.KNOWLEDGE_INQUIRY),
        agent_clients={AgentName.KNOWLEDGE: client}, synthesis_client=client)
    with pytest.raises(ContextBudgetExceededError):
        asyncio.run(runtime.ainvoke(messages("耳机能退吗？原因：" + "不能丢失的必要原因" * 5000)))
