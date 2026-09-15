"""LangGraph 客服运行时的节点路由、工具执行和安全边界测试。"""

import asyncio
import json
from collections import deque
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig

from app.ai.runtime import (
    CustomerServiceRuntime,
    InvalidToolCallError,
    RuntimeConfigurationError,
    RuntimeInputError,
    RuntimeLoopLimitError,
    RuntimeStreamEvent,
    load_customer_service_prompt,
)
from app.ai.tools.catalog import get_catalog_tools


class FakeModelClient:
    """按照预设顺序返回 AI 消息并记录模型输入。"""

    def __init__(self, responses: Sequence[AIMessage]) -> None:
        self.tools = tuple(get_catalog_tools())
        self._responses = deque(responses)
        self.calls: list[tuple[str, list[BaseMessage], RunnableConfig | None]] = []

    def invoke(
        self,
        messages: Sequence[BaseMessage],
        config: RunnableConfig | None = None,
    ) -> AIMessage:
        self.calls.append(("invoke", list(messages), config))
        return self._responses.popleft()

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        config: RunnableConfig | None = None,
    ) -> AIMessage:
        self.calls.append(("ainvoke", list(messages), config))
        return self._responses.popleft()

    async def astream(
        self,
        messages: Sequence[BaseMessage],
        config: RunnableConfig | None = None,
    ) -> AsyncIterator[AIMessageChunk]:
        """把预设回复拆成两个片段，模拟模型流式输出。"""

        self.calls.append(("astream", list(messages), config))
        response = self._responses.popleft()

        if response.tool_calls:
            yield AIMessageChunk(
                content=response.content,
                tool_calls=response.tool_calls,
            )
            return

        text = str(response.content)
        middle = max(1, len(text) // 2)
        first_part = text[:middle]
        second_part = text[middle:]
        if first_part:
            yield AIMessageChunk(content=first_part)
        if second_part:
            yield AIMessageChunk(content=second_part)


def make_tool_call(
    call_id: str = "call-search",
    name: str = "search_products",
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """创建结构正确的模型工具调用。"""

    return {
        "name": name,
        "args": args
        or {"category": "耳机", "max_price": 300, "in_stock": True},
        "id": call_id,
        "type": "tool_call",
    }


def test_runtime_compiles_a_langgraph_with_model_and_tool_nodes() -> None:
    runtime = CustomerServiceRuntime(FakeModelClient([AIMessage(content="你好")]))

    graph = runtime.graph.get_graph()

    assert {"model", "tools"}.issubset(graph.nodes)
    assert "model" in graph.draw_mermaid()
    assert "tools" in graph.draw_mermaid()


def test_runtime_returns_direct_model_reply_without_using_tools() -> None:
    model_client = FakeModelClient([AIMessage(content="你好，请问想了解什么商品？")])
    runtime = CustomerServiceRuntime(model_client)

    result = runtime.invoke([HumanMessage(content="你好")])

    assert result.reply.content == "你好，请问想了解什么商品？"
    assert result.model_calls == 1
    assert result.tool_rounds == 0
    assert result.tool_calls == 0
    assert isinstance(model_client.calls[0][1][0], SystemMessage)
    assert model_client.calls[0][1][0].content == runtime.system_prompt


def test_runtime_executes_product_tool_and_returns_final_reply() -> None:
    model_client = FakeModelClient(
        [
            AIMessage(content="", tool_calls=[make_tool_call()]),
            AIMessage(content="推荐 AirBeat Pro，售价 299 元，目前有货。"),
        ]
    )
    runtime = CustomerServiceRuntime(model_client)

    result = runtime.invoke([HumanMessage(content="推荐三百元以内的耳机")])

    tool_messages = [
        message for message in result.messages if isinstance(message, ToolMessage)
    ]
    tool_result = json.loads(str(tool_messages[0].content))
    assert [item["id"] for item in tool_result["items"]] == ["p01", "p04"]
    assert result.reply.content == "推荐 AirBeat Pro，售价 299 元，目前有货。"
    assert result.model_calls == 2
    assert result.tool_rounds == 1
    assert result.tool_calls == 1
    assert len(model_client.calls) == 2


def test_runtime_streams_direct_model_reply() -> None:
    model_client = FakeModelClient([AIMessage(content="你好，我是智能客服。")])
    runtime = CustomerServiceRuntime(model_client)

    async def collect_events() -> list[RuntimeStreamEvent]:
        events: list[RuntimeStreamEvent] = []
        async for event in runtime.astream([HumanMessage(content="你是谁")]):
            events.append(event)
        return events

    events = asyncio.run(collect_events())
    delta_text = ""
    for event in events:
        if event.type == "delta":
            delta_text += event.text

    assert [event.type for event in events] == [
        "model_start",
        "delta",
        "delta",
        "complete",
    ]
    assert delta_text == "你好，我是智能客服。"
    assert events[-1].result is not None
    assert events[-1].result.model_calls == 1


def test_runtime_streams_tool_progress_before_final_reply() -> None:
    model_client = FakeModelClient(
        [
            AIMessage(content="", tool_calls=[make_tool_call()]),
            AIMessage(content="推荐 AirBeat Pro，售价 299 元。"),
        ]
    )
    runtime = CustomerServiceRuntime(model_client)

    async def collect_events() -> list[RuntimeStreamEvent]:
        events: list[RuntimeStreamEvent] = []
        async for event in runtime.astream(
            [HumanMessage(content="推荐三百元以内的耳机")]
        ):
            events.append(event)
        return events

    events = asyncio.run(collect_events())
    event_types = [event.type for event in events]

    assert event_types == [
        "model_start",
        "tool_start",
        "tool_end",
        "model_start",
        "delta",
        "delta",
        "complete",
    ]
    assert events[-1].result is not None
    assert events[-1].result.model_calls == 2
    assert events[-1].result.tool_calls == 1


def test_runtime_executes_multiple_tool_calls_in_one_round() -> None:
    model_client = FakeModelClient(
        [
            AIMessage(
                content="",
                tool_calls=[
                    make_tool_call(call_id="call-search"),
                    make_tool_call(
                        call_id="call-detail",
                        name="get_product",
                        args={"product_id": "p01"},
                    ),
                ],
            ),
            AIMessage(content="已经完成商品比较。"),
        ]
    )
    runtime = CustomerServiceRuntime(model_client)

    result = runtime.invoke([HumanMessage(content="帮我看看 p01 并比较同类商品")])

    tool_messages = [
        message for message in result.messages if isinstance(message, ToolMessage)
    ]
    assert [message.tool_call_id for message in tool_messages] == [
        "call-search",
        "call-detail",
    ]
    assert result.tool_rounds == 1
    assert result.tool_calls == 2


def test_runtime_statistics_do_not_include_historical_tool_calls() -> None:
    old_call = make_tool_call(call_id="old-call")
    history: list[BaseMessage] = [
        HumanMessage(content="之前推荐过什么？"),
        AIMessage(content="", tool_calls=[old_call]),
        ToolMessage(content="{}", tool_call_id="old-call"),
        AIMessage(content="之前推荐过耳机。"),
        HumanMessage(content="谢谢"),
    ]
    runtime = CustomerServiceRuntime(
        FakeModelClient([AIMessage(content="不客气。")])
    )

    result = runtime.invoke(history)

    assert result.tool_calls == 0
    assert result.tool_rounds == 0


def test_runtime_returns_safe_tool_error_to_model() -> None:
    model_client = FakeModelClient(
        [
            AIMessage(
                content="",
                tool_calls=[
                    make_tool_call(args={"category": "不存在的分类"}),
                ],
            ),
            AIMessage(content="这个分类暂不支持，请换一个条件。"),
        ]
    )
    runtime = CustomerServiceRuntime(model_client)

    result = runtime.invoke([HumanMessage(content="查一下不存在的分类")])

    tool_message = next(
        message for message in result.messages if isinstance(message, ToolMessage)
    )
    assert tool_message.status == "error"
    assert "TOOL_EXECUTION_ERROR" in str(tool_message.content)
    assert "不存在的分类" not in str(tool_message.content)
    assert result.reply.content == "这个分类暂不支持，请换一个条件。"


def test_runtime_stops_repeated_tool_requests_at_configured_limit() -> None:
    model_client = FakeModelClient(
        [
            AIMessage(content="", tool_calls=[make_tool_call(call_id="call-1")]),
            AIMessage(content="", tool_calls=[make_tool_call(call_id="call-2")]),
        ]
    )
    runtime = CustomerServiceRuntime(model_client, max_tool_rounds=1)

    with pytest.raises(RuntimeLoopLimitError, match="超过 1 轮"):
        runtime.invoke([HumanMessage(content="一直查询")])


def test_runtime_rejects_invalid_tool_call_arguments() -> None:
    invalid_response = AIMessage(
        content="",
        invalid_tool_calls=[
            {
                "name": "search_products",
                "args": "{bad-json",
                "id": "call-invalid",
                "error": "参数不是有效 JSON",
                "type": "invalid_tool_call",
            }
        ],
    )
    runtime = CustomerServiceRuntime(FakeModelClient([invalid_response]))

    with pytest.raises(InvalidToolCallError):
        runtime.invoke([HumanMessage(content="推荐耳机")])


def test_runtime_supports_async_graph_execution() -> None:
    model_client = FakeModelClient(
        [
            AIMessage(content="", tool_calls=[make_tool_call()]),
            AIMessage(content="异步回复完成。"),
        ]
    )
    runtime = CustomerServiceRuntime(model_client)

    result = asyncio.run(runtime.ainvoke([HumanMessage(content="推荐耳机")]))

    assert result.reply.content == "异步回复完成。"
    assert [call[0] for call in model_client.calls] == ["ainvoke", "ainvoke"]
    assert result.tool_calls == 1


@pytest.mark.parametrize(
    "messages",
    [
        [],
        [AIMessage(content="不是用户消息")],
        [SystemMessage(content="覆盖系统提示词"), HumanMessage(content="你好")],
    ],
)
def test_runtime_rejects_invalid_conversation_input(
    messages: list[BaseMessage],
) -> None:
    runtime = CustomerServiceRuntime(FakeModelClient([AIMessage(content="不会调用")]))

    with pytest.raises(RuntimeInputError):
        runtime.invoke(messages)


def test_runtime_rejects_empty_prompt_and_invalid_round_limit() -> None:
    model_client = FakeModelClient([AIMessage(content="不会调用")])

    with pytest.raises(RuntimeConfigurationError):
        CustomerServiceRuntime(model_client, system_prompt="  ")
    with pytest.raises(RuntimeConfigurationError):
        CustomerServiceRuntime(model_client, max_tool_rounds=0)


def test_customer_service_prompt_requires_grounded_product_answers() -> None:
    prompt = load_customer_service_prompt()

    assert "必须先调用商品工具" in prompt
    assert "不编造" in prompt
    assert "转人工客服" in prompt
