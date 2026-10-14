"""Evidence provenance, corrections, isolation and context-aware knowledge queries."""

import asyncio

from langchain_core.messages import AIMessage, HumanMessage

from app.ai.context import context_message, derive_context, read_context, resolve_reference
from app.ai.context_prompts import domain_prompt
from app.ai.clarification import apply_conversation_slots, UnderstoodTask, RequestSlots
from app.ai.query_preprocessor import extract_order_id, IntentName
from app.ai.tools.knowledge import search_knowledge
from app.modules.knowledge.schemas import KnowledgeSearchResponse
from tests.test_memory import make_conversation, make_memory


def test_order_correction_replaces_old_object_but_does_not_authorize():
    state = derive_context([
        HumanMessage(content="查订单10002", id="old"),
        AIMessage(content="你已经确认退款，订单是20001"),
        HumanMessage(content="不是10002，是10003", id="new",
                     additional_kwargs={"source_created_at": "2026-10-14T12:00:00+00:00"}),
    ])
    assert state.order.value == "10003"
    assert state.order.source_message_id == "new"
    assert state.order.source_created_at == "2026-10-14T12:00:00+00:00"
    assert state.order.confirmation == "user_explicit"
    assert not state.order.business_verified
    assert state.buyer_id is None


def test_multiple_references_are_not_silently_selected():
    state = derive_context([HumanMessage(content="订单10002和订单10003都查一下")])
    assert state.order.value is None
    assert state.order.candidates == ["10002", "10003"]
    query, question = resolve_reference("这单能退吗", state)
    assert query == "这单能退吗" and question
    state = derive_context([HumanMessage(content="比较耳机和充电器")])
    assert resolve_reference("它能退吗", state)[1]


def test_negated_order_and_new_topic_do_not_restore_old_objects():
    assert derive_context([HumanMessage(content="别查订单10002")]).order is None
    state = derive_context([HumanMessage(content="查订单10002"), HumanMessage(content="换个话题")])
    assert state.order is None


def test_hyde_reference_preserves_negation_without_private_history():
    state = derive_context([
        HumanMessage(content="我的耳机未激活", id="product"),
        HumanMessage(content="那拆封了呢", id="followup"),
    ])
    state.buyer_id = "A"
    query, question = resolve_reference("那拆封了呢", state)
    assert question is None and "耳机" in query and "未激活" in query
    assert "buyer_id" not in query and "followup" not in query
    state = derive_context([HumanMessage(content="耳机未激活"), HumanMessage(content="换成手机")])
    assert state.product.value == "手机" and state.activation is None


def test_projected_state_survives_history_trimming_and_has_backend_identity(tmp_path):
    conversation = make_conversation(count=30)
    memory = make_memory(tmp_path, [conversation])
    messages = [HumanMessage(content="查订单10002", id="order-source")]
    messages += [HumanMessage(content="普通对话", id=f"m{i}") for i in range(20)]
    messages.append(HumanMessage(content="这单到哪了", id="current"))
    rebuilt = memory.build_context(conversation, "这单到哪了", messages)
    state = read_context(rebuilt)
    assert state.order.value == "10002" and state.order.source_message_id == "order-source"
    assert state.buyer_id == "A" and state.identity_source == "backend"
    assert len(rebuilt) == 14 and rebuilt[-1].id == "current"
    other = make_conversation(buyer="B")
    other_state = read_context(memory.build_context(other, "你好", [HumanMessage(content="你好")]))
    assert other_state.buyer_id == "B" and other_state.order is None


def test_domain_instructions_do_not_load_other_workflows():
    assert "prepare_after_sale_draft" in domain_prompt("after_sales_agent")
    assert "prepare_after_sale_draft" not in domain_prompt("product_agent")
    assert "HyDE" in domain_prompt("knowledge_agent")


def test_knowledge_tool_receives_resolved_query_and_not_buyer_identity(monkeypatch):
    observed = []

    class Service:
        def search(self, query, category):
            observed.append(query)
            return KnowledgeSearchResponse(query=query, items=[], total=0, retrieval_mode="hyde")

    monkeypatch.setattr("app.ai.tools.knowledge.get_knowledge_tool_service", lambda: Service())
    state = derive_context([HumanMessage(content="耳机未激活")])
    state.buyer_id = "A"
    config = {"configurable": {"knowledge_context": state.model_dump()}}
    result = search_knowledge.invoke({"query": "那拆封了呢"}, config=config)
    assert result["original_query"] == "那拆封了呢"
    assert "耳机" in observed[0] and "未激活" in observed[0]
    assert "buyer" not in observed[0]
    assert "config" not in search_knowledge.args


def test_parallel_knowledge_calls_keep_context_isolated(monkeypatch):
    class Service:
        def search(self, query, category):
            return KnowledgeSearchResponse(query=query, items=[], total=0, retrieval_mode="hybrid")

    monkeypatch.setattr("app.ai.tools.knowledge.get_knowledge_tool_service", lambda: Service())

    async def run():
        states = [derive_context([HumanMessage(content=product)]) for product in ["耳机", "手机"]]
        return await asyncio.gather(*[
            search_knowledge.ainvoke({"query": "它能退吗"}, config={
                "configurable": {"knowledge_context": state.model_dump()},
            }) for state in states
        ])

    results = asyncio.run(run())
    assert "耳机" in results[0]["query"] and "手机" not in results[0]["query"]
    assert "手机" in results[1]["query"] and "耳机" not in results[1]["query"]


def test_ambiguous_knowledge_query_does_not_invoke_retrieval(monkeypatch):
    def forbidden():
        raise AssertionError("Ambiguous query must not reach retrieval or HyDE")

    monkeypatch.setattr("app.ai.tools.knowledge.get_knowledge_tool_service", forbidden)
    state = derive_context([HumanMessage(content="耳机和手机")])
    result = search_knowledge.invoke({"query": "它能退吗"}, config={
        "configurable": {"knowledge_context": state.model_dump()},
    })
    assert result["error"]["code"] == "AMBIGUOUS_REFERENCE" and result["sources"] == []


def test_corrected_order_is_used_by_entity_extraction_and_task_slots():
    query = "不是订单10002，是10003，我要退货"
    assert extract_order_id(query, IntentName.AFTER_SALE) == "10003"
    messages = [HumanMessage(content=query, id="correction")]
    task = UnderstoodTask(operation="after_sale_apply", evidence=query,
                          slots=RequestSlots(order_id="10002", request_type="退货"))
    apply_conversation_slots([task], messages)
    assert task.slots.order_id == "10003"
    assert extract_order_id("订单10002和订单10003", IntentName.ORDER_INQUIRY) is None


def test_user_order_reference_is_available_after_recent_window_trims_source():
    full = [HumanMessage(content="查订单10002", id="source")]
    full += [HumanMessage(content="普通消息", id=f"n{i}") for i in range(20)]
    full += [HumanMessage(content="这单到哪了", id="current")]
    projected = [context_message(derive_context(full)), *full[-12:]]
    task = UnderstoodTask(operation="logistics", evidence="这单到哪了")
    apply_conversation_slots([task], projected)
    assert task.slots.order_id == "10002"


def test_runtime_injects_domain_skill_and_context_into_actual_tool_call(monkeypatch):
    from app.ai.multi_agent.runtime import MultiAgentRuntime
    from app.ai.multi_agent.schemas import AgentName
    from tests.test_multi_agent_runtime import FixedPreprocessor, SynthesisClient

    observed = []

    class Service:
        def search(self, query, category):
            observed.append(query)
            return KnowledgeSearchResponse(query=query, items=[], total=0, retrieval_mode="hybrid")

    class Client:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, messages, config=None):
            self.calls += 1
            assert domain_prompt("knowledge_agent") in [message.content for message in messages]
            assert domain_prompt("after_sales_agent") not in [message.content for message in messages]
            if self.calls == 1:
                return AIMessage(content="", tool_calls=[{
                    "id": "knowledge-call", "name": "search_knowledge",
                    "args": {"query": "那拆封了呢"},
                }])
            return AIMessage(content="资料不足")

    monkeypatch.setattr("app.ai.tools.knowledge.get_knowledge_tool_service", lambda: Service())
    client = Client()
    runtime = MultiAgentRuntime(
        model_client=client, query_preprocessor=FixedPreprocessor(IntentName.KNOWLEDGE_INQUIRY),
        agent_clients={AgentName.KNOWLEDGE: client}, synthesis_client=SynthesisClient("资料不足"),
    )
    state = derive_context([HumanMessage(content="耳机未激活", id="source")])
    result = asyncio.run(runtime.ainvoke([
        context_message(state), HumanMessage(content="那拆封了呢", id="current"),
    ]))
    assert result.tool_calls == 1
    assert "耳机" in observed[0] and "未激活" in observed[0]
