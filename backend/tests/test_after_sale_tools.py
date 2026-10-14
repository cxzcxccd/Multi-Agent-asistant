"""LangGraph 售后工具的身份隔离、只读查询和确认草稿测试。"""

import pytest
import asyncio
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.ai.clarification import QueryUnderstanding, RequestSlots, UnderstoodTask
from app.ai.multi_agent.runtime import MultiAgentRuntime
from app.ai.multi_agent.schemas import AgentName
from app.ai.query_preprocessor import IntentName
from app.ai.runtime import RuntimePause
from tests.test_multi_agent_runtime import DirectClient, FixedPreprocessor, SynthesisClient

from app.ai.tools import after_sales
from app.ai.tools.after_sales import (
    get_after_sale_tools,
    prepare_after_sale_draft,
)
from app.ai.tools.orders import get_order_service


class EmptyAfterSaleService:
    """复用订单种子，但隔离用户本地可能已经创建的售后记录。"""

    def __init__(self) -> None:
        self.order_service = get_order_service()

    def list_for_buyer(self, _buyer_id: str) -> list[object]:
        return []


@pytest.fixture(autouse=True)
def isolated_after_sale_service(monkeypatch: pytest.MonkeyPatch) -> None:
    service = EmptyAfterSaleService()
    monkeypatch.setattr(after_sales, "get_after_sale_tool_service", lambda: service)


def buyer_config(buyer_id: str) -> dict[str, dict[str, str]]:
    """创建由会话服务注入的可信买家上下文。"""

    return {"configurable": {"buyer_id": buyer_id}}


def test_after_sale_tools_have_stable_names() -> None:
    tools = get_after_sale_tools()

    assert [item.name for item in tools] == [
        "list_after_sales",
        "get_after_sale",
        "prepare_after_sale_draft",
    ]
    assert set(prepare_after_sale_draft.args) == {
        "order_id",
        "request_type",
        "reason",
    }


def test_prepare_after_sale_draft_returns_confirmation_artifact_without_writing() -> None:
    result = prepare_after_sale_draft.invoke(
        {
            "order_id": "10002",
            "request_type": "退货",
            "reason": "左耳没有声音，已经更换设备测试",
        },
        config=buyer_config("A"),
    )

    assert result == {
        "success": True,
        "artifact": {
            "type": "after_sale_draft",
            "requires_confirmation": True,
            "draft": {
                "order_id": "10002",
                "request_type": "退货",
                "reason": "左耳没有声音，已经更换设备测试",
            },
        },
    }


def test_prepare_after_sale_draft_enforces_buyer_identity() -> None:
    foreign_order = prepare_after_sale_draft.invoke(
        {
            "order_id": "20001",
            "request_type": "退货",
            "reason": "商品出现故障，申请退货",
        },
        config=buyer_config("A"),
    )
    assert foreign_order["error"]["code"] == "ORDER_NOT_ACCESSIBLE"


@pytest.mark.parametrize("order_id", ["10004", "10001", "10002", "10003"])
def test_all_order_stages_can_prepare_return_draft(order_id: str) -> None:
    result = prepare_after_sale_draft.invoke(
        {"order_id": order_id, "request_type": "退货", "reason": "七天无理由"},
        config=buyer_config("A"),
    )
    assert result["success"] is True
    assert result["artifact"]["requires_confirmation"] is True
    assert result["artifact"]["draft"] == {
        "order_id": order_id, "request_type": "退货", "reason": "七天无理由",
    }


def test_draft_can_be_previewed_before_buyer_provides_reason() -> None:
    result = prepare_after_sale_draft.invoke(
        {"order_id": "10002", "request_type": "退货"}, config=buyer_config("A"),
    )
    assert result["success"] is True
    assert result["artifact"]["requires_confirmation"] is True
    assert result["artifact"]["draft"]["reason"] == ""


@pytest.mark.parametrize("order_id", ["10004", "10001", "10002"])
def test_return_request_pauses_with_empty_draft_before_domain_model_can_skip_preview(order_id: str) -> None:
    class Understanding:
        async def analyze(self, messages, pending=None):
            return QueryUnderstanding(tasks=[UnderstoodTask(
                operation="after_sale_apply", evidence=f"订单{order_id}申请退货",
                slots=RequestSlots(order_id=order_id, request_type="退货"),
            )])

    async def scenario():
        calls = []
        runtime = MultiAgentRuntime(
            model_client=object(), query_preprocessor=FixedPreprocessor(IntentName.AFTER_SALE),
            agent_clients={AgentName.AFTER_SALES: DirectClient("直接转人工", calls, "after_sale")},
            synthesis_client=SynthesisClient("已转交人工审核"),
            understanding_service=Understanding(), checkpointer=InMemorySaver(),
        )
        config = {"configurable": {"thread_id": "return-preview", "buyer_id": "A"}}
        paused = await runtime.ainvoke([HumanMessage(content=f"订单{order_id}申请退货")], config=config)
        assert isinstance(paused, RuntimePause)
        assert paused.confirmation["draft"]["reason"] == ""
        assert paused.tool_calls == 1
        assert calls == []
        completed = await runtime.aresume({"action": "confirm", "draft": {
            "order_id": order_id, "request_type": "退货", "reason": "七天无理由",
        }}, config=config)
        assert completed.reply.text == "已转交人工审核"

    asyncio.run(scenario())
