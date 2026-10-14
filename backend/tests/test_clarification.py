"""追问、指代恢复和执行门禁的行为测试。"""

import asyncio
import pytest
from pydantic import ValidationError

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.ai.clarification import (
    BusinessReference, QueryUnderstanding, RequestSlots, UnderstoodTask,
    collect_references, find_missing_fields, resolve_references, tool_precondition,
    QueryUnderstandingService, ClarificationRequest,
)
from app.ai.checkpoints import open_sqlite_checkpointer
from app.ai.multi_agent.runtime import MultiAgentRuntime
from app.ai.multi_agent.schemas import AgentName
from app.ai.query_preprocessor import IntentName
from tests.test_multi_agent_runtime import DirectClient, FixedPreprocessor, SynthesisClient


def test_policy_and_order_list_do_not_require_order_number():
    tasks = [
        UnderstoodTask(operation="policy", evidence="退款一般多久到账"),
        UnderstoodTask(operation="order_list", evidence="查询我的订单"),
    ]
    assert find_missing_fields(tasks) == []


def test_after_sale_preview_requires_type_but_can_leave_reason_empty():
    task = UnderstoodTask(
        operation="after_sale_apply", evidence="订单10002申请售后",
        slots=RequestSlots(order_id="10002"),
    )
    assert find_missing_fields([task]) == ["request_type"]
    task.slots.request_type = "退货"
    assert find_missing_fields([task]) == []
    assert tool_precondition("prepare_after_sale_draft", {
        "order_id": "10002", "request_type": "退货", "reason": "",
    }, [task]) is None
    assert tool_precondition("prepare_after_sale_draft", {
        "order_id": "10002", "request_type": "退货", "reason": "编造的质量问题",
    }, [task]) is not None


def test_multiple_orders_are_not_resolved_by_guessing():
    task = UnderstoodTask(operation="order_detail", evidence="查这个订单")
    references = [BusinessReference(kind="order", id="10001"), BusinessReference(kind="order", id="10002")]
    resolve_references([task], references)
    assert task.slots.order_id is None
    task.slots.reference_position = 2
    resolve_references([task], references)
    assert task.slots.order_id == "10002"


def test_personal_refund_cannot_use_knowledge_tool():
    task = UnderstoodTask(operation="refund_detail", evidence="查我的退款")
    assert tool_precondition("search_knowledge", {"query": "退款规则"}, [task]) is not None


def test_manufactured_order_number_is_blocked():
    task = UnderstoodTask(
        operation="order_detail", evidence="查订单10001", slots=RequestSlots(order_id="10001"),
    )
    assert tool_precondition("get_order", {"order_id": "10001"}, [task]) is None
    assert tool_precondition("get_order", {"order_id": "10002"}, [task]) is not None


def test_product_reference_order_comes_from_real_search_results():
    references = collect_references([{
        "name": "search_products", "output": {"items": [{"id": "P2"}, {"id": "P1"}]},
    }])
    task = UnderstoodTask(
        operation="product_detail", evidence="第二个", slots=RequestSlots(reference_position=2),
    )
    resolve_references([task], references)
    assert task.slots.product_id == "P1"


def test_latest_search_replaces_old_reference_list():
    references = collect_references([
        {"name": "search_products", "output": {"items": [{"id": "P1"}, {"id": "P2"}]}},
        {"name": "search_products", "output": {"items": [{"id": "P3"}, {"id": "P4"}]}},
        {"name": "get_product", "output": {"success": True, "product": {"id": "P3"}}},
    ])
    task = UnderstoodTask(operation="product_detail", evidence="第二个", slots=RequestSlots(reference_position=2))
    resolve_references([task], references)
    assert task.slots.product_id == "P4"


def test_missing_tool_arguments_are_blocked_before_execution():
    task = UnderstoodTask(operation="order_detail", evidence="查订单10001", slots=RequestSlots(order_id="10001"))
    assert tool_precondition("get_order", {}, [task]) is not None


class UnusedUnderstandingModel:
    def bind_tools(self, *args, **kwargs):
        return self

    async def ainvoke(self, *args, **kwargs):
        raise AssertionError("明确短语不应额外调用需求提取模型")


@pytest.mark.parametrize("query", ["七天无理由", "7天无理由退货", "七天无理由退货的条件"])
def test_explicit_short_policy_is_understood_without_extra_model_call(query):
    service = QueryUnderstandingService(UnusedUnderstandingModel())
    result = asyncio.run(service.analyze([HumanMessage(content=query)]))
    assert result.tasks[0].operation == "policy"
    assert find_missing_fields(result.tasks) == []


def test_short_reason_continues_pending_return_request():
    service = QueryUnderstandingService(UnusedUnderstandingModel())
    pending = ClarificationRequest(original_query="订单10004退货", tasks=[UnderstoodTask(
        operation="after_sale_apply", evidence="订单10004退货",
        slots=RequestSlots(order_id="10004", request_type="退货"),
    )], missing_fields=["reason"], question="请补充退货原因")
    result = asyncio.run(service.analyze([HumanMessage(content="七天无理由")], pending))
    task = result.tasks[0]
    assert task.operation == "after_sale_apply"
    assert task.slots.order_id == "10004"
    assert task.slots.reason == "七天无理由"
    assert find_missing_fields(result.tasks) == []


def test_short_reason_uses_user_order_and_never_assistant_example_order():
    service = QueryUnderstandingService(UnusedUnderstandingModel())
    history = [HumanMessage(content="订单10004退货"), AIMessage(content="请补充原因，例如订单10002商品损坏。"), HumanMessage(content="七天无理由")]
    task = asyncio.run(service.analyze(history)).tasks[0]
    assert task.slots.order_id == "10004"
    assert task.operation == "after_sale_apply"
    history[0] = HumanMessage(content="我想退货")
    task = asyncio.run(service.analyze(history)).tasks[0]
    assert task.slots.order_id is None
    assert find_missing_fields([task]) == ["order_id"]
    history[1] = AIMessage(content="草稿已取消，未保留。如需继续请补充原因。")
    assert asyncio.run(service.analyze(history)).tasks[0].operation == "policy"


def test_misplaced_model_slots_are_normalized_without_disabling_validation():
    task = UnderstoodTask.model_validate({
        "operation": "after_sale_apply", "evidence": "七天无理由",
        "request_type": "退货", "order_id": "10004", "reason": "七天无理由",
    })
    assert task.slots.order_id == "10004"
    with pytest.raises(ValidationError):
        UnderstoodTask.model_validate({"operation": "policy", "evidence": "规则", "unknown": "value"})
    with pytest.raises(ValidationError):
        UnderstoodTask.model_validate({"operation": "after_sale_apply", "evidence": "退货", "order_id": "10004", "slots": {"order_id": "10002"}})


def test_normalized_model_slots_still_require_user_evidence():
    class Model:
        order_id = "10004"

        def bind_tools(self, *args, **kwargs):
            return self

        async def ainvoke(self, *args, **kwargs):
            return AIMessage(content="", tool_calls=[{
                "id": "understanding", "name": "QueryUnderstanding", "args": {"tasks": [{
                    "operation": "after_sale_apply", "evidence": "申请退货",
                    "order_id": self.order_id, "request_type": "退货", "reason": "不喜欢这个颜色",
                }]},
            }])

    model = Model()
    service = QueryUnderstandingService(model)
    messages = [HumanMessage(content="订单10004申请退货，不喜欢这个颜色")]
    assert asyncio.run(service.analyze(messages)).tasks[0].slots.order_id == "10004"
    model.order_id = "10002"
    with pytest.raises(ValueError, match="缺少原话依据"):
        asyncio.run(service.analyze(messages))


def test_pending_question_survives_checkpoint_and_other_thread_is_isolated():
    class Understanding:
        async def analyze(self, messages, pending=None):
            if str(messages[-1].content) == "10001":
                assert pending is not None
                return QueryUnderstanding(tasks=[UnderstoodTask(
                    operation="order_detail", evidence="10001", slots=RequestSlots(order_id="10001"),
                )])
            return QueryUnderstanding(tasks=[UnderstoodTask(operation="order_detail", evidence="查订单")])

    async def scenario():
        calls = []
        saver = InMemorySaver()
        config = {"configurable": {"thread_id": "conversation-one"}}
        arguments = {
            "model_client": object(), "query_preprocessor": FixedPreprocessor(IntentName.ORDER_INQUIRY),
            "agent_clients": {AgentName.ORDER: DirectClient("订单已发货", calls, "order")},
            "synthesis_client": SynthesisClient("订单已发货"),
            "understanding_service": Understanding(), "checkpointer": saver,
        }
        runtime = MultiAgentRuntime(**arguments)
        first = await runtime.ainvoke([HumanMessage(content="查订单")], config=config)
        assert "订单编号" in first.reply.text
        assert calls == []
        # 创建新的运行时实例，证明待处理任务不是靠实例内字典维持。
        runtime = MultiAgentRuntime(**arguments)
        second = await runtime.ainvoke([HumanMessage(content="10001")], config=config)
        assert second.reply.text == "订单已发货"
        assert calls == ["order"]
        snapshot = await runtime.graph.aget_state(config)
        assert snapshot.values["pending_clarification"] is None
        other = await runtime.graph.aget_state({"configurable": {"thread_id": "conversation-two"}})
        assert not other.values

    asyncio.run(scenario())


def test_pending_slots_survive_sqlite_connection_reopen(tmp_path):
    class Understanding:
        async def analyze(self, messages, pending=None):
            if pending is None:
                return QueryUnderstanding(tasks=[UnderstoodTask(
                    operation="after_sale_apply", evidence="订单10002申请售后",
                    slots=RequestSlots(order_id="10002"),
                )])
            assert pending.tasks[0].slots.order_id == "10002"
            return QueryUnderstanding(tasks=[UnderstoodTask(
                operation="after_sale_apply", evidence="耳机坏了",
                slots=RequestSlots(order_id="10002", request_type="退货", reason="耳机坏了"),
            )])

    async def scenario():
        path = (tmp_path / "clarification.db").as_posix()
        calls = []
        arguments = {
            "model_client": object(), "query_preprocessor": FixedPreprocessor(IntentName.AFTER_SALE),
            "agent_clients": {AgentName.AFTER_SALES: DirectClient("准备草稿", calls, "after_sale")},
            "synthesis_client": SynthesisClient("已准备草稿"), "understanding_service": Understanding(),
        }
        config = {"configurable": {"thread_id": "persistent-conversation"}}
        async with open_sqlite_checkpointer(path) as saver:
            runtime = MultiAgentRuntime(**arguments, checkpointer=saver)
            first = await runtime.ainvoke([HumanMessage(content="订单10002申请售后")], config=config)
            assert "退货还是换货" in first.reply.text
            assert not calls
        async with open_sqlite_checkpointer(path) as saver:
            runtime = MultiAgentRuntime(**arguments, checkpointer=saver)
            second = await runtime.ainvoke([HumanMessage(content="我要退货，耳机坏了")], config=config)
            assert second.reply.text == "已准备草稿"
            assert calls == ["after_sale"]

    asyncio.run(scenario())
