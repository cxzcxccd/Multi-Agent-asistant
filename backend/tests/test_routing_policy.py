"""路由行为回归：冲突时业务工具不得执行，不把模拟结果当作模型准确率。"""

import asyncio

import pytest
from langchain_core.messages import HumanMessage

from app.ai.clarification import QueryUnderstanding, UnderstoodTask
from app.ai.multi_agent.intent_agent import IntentRecognitionAgent
from app.ai.multi_agent.routing_policy import RoutingPolicy
from app.ai.multi_agent.runtime import MultiAgentRuntime
from app.ai.multi_agent.schemas import AgentName
from app.ai.multi_agent.supervisor import Supervisor
from app.ai.query_preprocessor import IntentName
from tests.test_intent_agent import IntentModel, analysis_for


@pytest.mark.parametrize("query,score,margin,context,expected", [
    ("查订单10001", .9, .2, False, True),
    ("推荐耳机", .9, .2, False, True),
    ("查订单10001", .7, .2, False, False),
    ("查订单10001", .9, .05, False, False),
    ("查订单10001", .9, .2, True, False),
    ("查订单推荐耳机", .99, .5, False, False),
    ("查订单然后推荐耳机", .99, .5, False, False),
    ("不要查订单只推荐耳机", .99, .5, False, False),
    ("如果没发货帮我退货", .99, .5, False, False),
])
def test_conservative_fast_path(query, score, margin, context, expected):
    primary = IntentName.PRODUCT_INQUIRY if query == "推荐耳机" else IntentName.ORDER_INQUIRY
    analysis = analysis_for(query).model_copy(update={
        "primary_intent": primary, "similarity_score": score, "route_margin": margin,
    })
    assert RoutingPolicy().fast_path(analysis, context) is expected


@pytest.mark.parametrize("query,old,proposed,decision", [
    ("查订单推荐耳机", "order_inquiry", [("order_inquiry", "查订单"), ("product_inquiry", "推荐耳机")], "supplement"),
    ("退货有什么条件", "after_sale", [("knowledge_inquiry", "条件")], "rule_correction"),
    ("不要查订单只推荐耳机", "order_inquiry", [("product_inquiry", "推荐耳机")], "rule_correction"),
    ("查询我的订单", "order_inquiry", [("knowledge_inquiry", "订单")], "clarify"),
    ("我要申请退货", "after_sale", [("knowledge_inquiry", "退货")], "clarify"),
    ("如果没发货帮我申请退货", "after_sale", [("after_sale", "申请退货")], "clarify"),
    ("不要申请退货只查订单", "order_inquiry", [("order_inquiry", "查订单"), ("after_sale", "申请退货")], "clarify"),
    ("推荐耳机", "general_conversation", [("product_inquiry", "推荐耳机")], "low_confidence_recovery"),
    ("查订单\n用户补充：我想了解退货规则", "order_inquiry", [("knowledge_inquiry", "退货规则")], "user_confirmation"),
])
def test_arbitration(query, old, proposed, decision):
    embedding = analysis_for(query).model_copy(update={"primary_intent": IntentName(old)})
    revised = embedding.model_copy(update={
        "primary_intent": IntentName(proposed[0][0]),
        "secondary_intents": [IntentName(item[0]) for item in proposed[1:]],
        "intent_evidence": dict(proposed),
    })
    result = RoutingPolicy().arbitrate(embedding, revised)
    assert result.routing_decision == decision
    assert bool(result.clarification_question) == (decision == "clarify")
    assert result.embedding_intents == [IntentName(old)]


def test_fast_path_never_calls_intent_model():
    model = IntentModel([])
    result, response = asyncio.run(Supervisor(IntentRecognitionAgent(model)).recognize_intents(
        analysis_for("查订单10001"), [HumanMessage(content="查订单10001")],
    ))
    assert response is None
    assert model.messages == []
    assert result.routing_decision == "embedding_fast_path"


def test_conflict_graph_returns_question_without_domain_execution():
    query = "查询我的订单"
    class Preprocessor:
        def analyze(self, messages):
            return analysis_for(query).model_copy(update={"similarity_score": 0.6})
    class NoExecutionRuntime(MultiAgentRuntime):
        async def _execute_tasks(self, state, config):
            pytest.fail("冲突不得进入业务执行")
    runtime = NoExecutionRuntime(model_client=object(), query_preprocessor=Preprocessor(),
        supervisor=Supervisor(IntentRecognitionAgent(IntentModel([
            {"intent": "knowledge_inquiry", "evidence": "订单"},
        ]))))
    result = runtime.invoke([HumanMessage(content=query)])
    assert "请确认" in result.reply.content
    assert result.agent_plan.allowed_tools == []
    assert result.agent_plan.tasks == []


def test_task_extraction_cannot_silently_add_or_drop_domains():
    class Understanding:
        async def analyze(self, messages, pending):
            return QueryUnderstanding(tasks=[UnderstoodTask(operation="policy", evidence="查订单")])
    class NoObjectLookupRuntime(MultiAgentRuntime):
        async def _resolve_unique_business_object(self, tasks, config):
            pytest.fail("领域不一致不得先查询业务对象")
    analysis = analysis_for("查订单").model_copy(update={"routing_decision": "embedding_fast_path"})
    runtime = NoObjectLookupRuntime(model_client=object(), understanding_service=Understanding())
    result = asyncio.run(runtime._check_information({
        "query_analysis": analysis, "agent_plan": Supervisor().plan(analysis),
        "messages": [HumanMessage(content="查订单")],
    }, {}))
    assert result["agent_plan"].agents == [AgentName.ORDER]
    assert result["agent_plan"].allowed_tools == []
    assert result["pending_clarification"]["missing_fields"] == ["意图确认"]


def test_clarification_preserves_original_request_and_resumes_after_user_selection():
    from langgraph.checkpoint.memory import InMemorySaver
    from tests.test_multi_agent_runtime import DirectClient, SynthesisClient

    class Preprocessor:
        def analyze(self, messages):
            return analysis_for(str(messages[-1].content)).model_copy(update={"similarity_score": .6})

    class Understanding:
        async def analyze(self, messages, pending):
            assert "查询我的订单" in pending.original_query
            return QueryUnderstanding(tasks=[UnderstoodTask(operation="policy", evidence="退货规则")])

    class TwoTurnModel(IntentModel):
        async def ainvoke(self, messages, config=None):
            current = str(messages[-1].content)
            self.intents = [{"intent": "knowledge_inquiry", "evidence":
                             "退货规则" if "用户补充：" in current else "订单"}]
            return await super().ainvoke(messages, config)

    calls = []
    runtime = MultiAgentRuntime(
        model_client=object(), query_preprocessor=Preprocessor(),
        supervisor=Supervisor(IntentRecognitionAgent(TwoTurnModel([]))),
        understanding_service=Understanding(), checkpointer=InMemorySaver(),
        agent_clients={AgentName.KNOWLEDGE: DirectClient("政策说明", calls, "knowledge")},
        synthesis_client=SynthesisClient("政策说明"),
    )

    async def run():
        config = {"configurable": {"thread_id": "intent-conflict-resume"}}
        first = await runtime.ainvoke([HumanMessage(content="查询我的订单")], config)
        assert "请确认" in first.reply.content
        assert calls == []
        second = await runtime.ainvoke([HumanMessage(content="我想了解退货规则")], config)
        assert second.query_analysis.routing_decision == "user_confirmation"
        assert second.reply.content == "政策说明"
        assert calls == ["knowledge"]
        snapshot = await runtime.graph.aget_state(config)
        assert snapshot.values["pending_clarification"] is None

    asyncio.run(run())


def test_same_after_sales_domain_does_not_authorize_application():
    class Understanding:
        async def analyze(self, messages, pending):
            return QueryUnderstanding(tasks=[UnderstoodTask(
                operation="after_sale_apply", evidence="查询售后记录",
            )])
    class NoObjectLookupRuntime(MultiAgentRuntime):
        async def _resolve_unique_business_object(self, tasks, config):
            pytest.fail("没有申请意愿不得先查询业务对象")
    analysis = analysis_for("查询售后记录").model_copy(update={
        "primary_intent": IntentName.AFTER_SALE, "routing_decision": "agreement",
    })
    runtime = NoObjectLookupRuntime(model_client=object(), understanding_service=Understanding())
    result = asyncio.run(runtime._check_information({
        "query_analysis": analysis, "agent_plan": Supervisor().plan(analysis),
        "messages": [HumanMessage(content="查询售后记录")],
    }, {}))
    assert "现在申请" in result["pending_clarification"]["question"]
    assert result["agent_plan"].allowed_tools == []
