"""Supervisor 混合意图识别与运行时接线测试，不访问在线模型。"""

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.ai.multi_agent.intent_agent import IntentRecognitionAgent
from app.ai.multi_agent.runtime import MultiAgentRuntime
from app.ai.multi_agent.schemas import AgentName
from app.ai.multi_agent.supervisor import Supervisor
from app.ai.query_preprocessor import IntentName
from app.ai.runtime import RuntimeErrorBase
from tests.test_multi_agent import make_analysis


class IntentModel:
    def __init__(self, intents, name="IntentRecognition"):
        self.intents = intents
        self.name = name
        self.messages = []

    def bind_tools(self, tools, **kwargs):
        assert tools[0].__name__ == "IntentRecognition"
        return self

    async def ainvoke(self, messages, config=None):
        self.messages = messages
        return AIMessage(content="", tool_calls=[{
            "name": self.name, "args": {"intents": self.intents}, "id": "intent-1",
        }])


def analysis_for(query):
    return make_analysis(IntentName.ORDER_INQUIRY).model_copy(update={
        "original_query": query, "normalized_query": query,
    })


def test_supervisor_recovers_unpunctuated_multiple_intents_and_tracks_usage():
    query = "查订单10001推荐耳机"
    model = IntentModel([
        {"intent": "order_inquiry", "evidence": "查订单10001"},
        {"intent": "product_inquiry", "evidence": "推荐耳机"},
    ])
    supervisor = Supervisor(IntentRecognitionAgent(model))
    runtime = MultiAgentRuntime(model_client=object(), supervisor=supervisor)
    state = {"query_analysis": analysis_for(query), "messages": [HumanMessage(content=query)],
             "model_calls": 0, "model_usage": []}
    result = asyncio.run(runtime._supervise(state, {}))
    assert result["agent_plan"].agents == [AgentName.ORDER, AgentName.PRODUCT]
    assert result["query_analysis"].secondary_intents == [IntentName.PRODUCT_INQUIRY]
    assert result["query_analysis"].router_source == "embedding+supervisor_intent_agent"
    assert result["query_analysis"].similarity_score == 0.9
    assert result["model_calls"] == 1
    assert result["model_usage"][0]["stage"] == "intent_recognition"
    assert "embedding参考" in model.messages[0].content


def test_agent_can_correct_embedding_without_adding_negated_intent():
    query = "不要查订单只推荐耳机"
    model = IntentModel([{"intent": "product_inquiry", "evidence": "推荐耳机"}])
    revised, _ = asyncio.run(IntentRecognitionAgent(model).analyze(
        analysis_for(query), [HumanMessage(content=query)],
    ))
    assert revised.primary_intent == IntentName.PRODUCT_INQUIRY
    assert revised.secondary_intents == []
    assert "否定" in model.messages[0].content


@pytest.mark.parametrize("intents,name", [
    ([{"intent": "order_inquiry", "evidence": "虚构请求"}], "IntentRecognition"),
    ([{"intent": "order_inquiry", "evidence": "查订单"}] * 2, "IntentRecognition"),
    ([{"intent": "unknown", "evidence": "查订单"}], "IntentRecognition"),
    ([{"intent": "order_inquiry", "evidence": "查订单"}], "get_order"),
])
def test_agent_rejects_untrusted_classification(intents, name):
    with pytest.raises(ValueError):
        asyncio.run(IntentRecognitionAgent(IntentModel(intents, name)).analyze(
            analysis_for("查订单"), [HumanMessage(content="查订单")],
        ))


def test_runtime_stops_before_dispatch_when_recognition_is_invalid():
    supervisor = Supervisor(IntentRecognitionAgent(IntentModel([
        {"intent": "order_inquiry", "evidence": "虚构证据"},
    ])))
    runtime = MultiAgentRuntime(model_client=object(), supervisor=supervisor)
    with pytest.raises(RuntimeErrorBase, match="辅助意图识别暂时不可用"):
        asyncio.run(runtime._supervise({
            "query_analysis": analysis_for("查订单").model_copy(update={"similarity_score": 0.6}),
            "messages": [HumanMessage(content="查订单")],
        }, {}))
