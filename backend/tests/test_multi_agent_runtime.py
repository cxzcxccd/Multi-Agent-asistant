"""任务拆解、并行／顺序执行和汇总 Agent 测试。"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from app.ai.multi_agent.runtime import MultiAgentRuntime
from app.ai.multi_agent.schemas import AgentName
from app.ai.query_preprocessor import IntentName
from tests.test_multi_agent import make_analysis


class FixedPreprocessor:
    def __init__(
        self,
        primary: IntentName,
        secondary: list[IntentName] | None = None,
    ) -> None:
        self.analysis = make_analysis(primary, secondary)

    def analyze(self, _messages: object) -> Any:
        return self.analysis


class DirectClient:
    """返回固定结果并记录调用顺序的领域 Agent 替身。"""

    def __init__(self, text: str, calls: list[str], name: str) -> None:
        self.text = text
        self.calls = calls
        self.name = name

    async def ainvoke(
        self,
        _messages: Sequence[BaseMessage],
        config: RunnableConfig | None = None,
    ) -> AIMessage:
        del config
        self.calls.append(self.name)
        return AIMessage(content=self.text)


class ParallelClient(DirectClient):
    """两个客户端都开始后才返回，用于证明任务并发执行。"""

    def __init__(
        self,
        text: str,
        calls: list[str],
        name: str,
        ready: asyncio.Event,
    ) -> None:
        super().__init__(text, calls, name)
        self.ready = ready

    async def ainvoke(
        self,
        _messages: Sequence[BaseMessage],
        config: RunnableConfig | None = None,
    ) -> AIMessage:
        del config
        self.calls.append(self.name)
        if len(self.calls) >= 2:
            self.ready.set()
        await asyncio.wait_for(self.ready.wait(), timeout=1)
        return AIMessage(content=self.text)


class SynthesisClient:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.messages: list[BaseMessage] = []

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        config: RunnableConfig | None = None,
    ) -> AIMessage:
        del config
        self.messages = list(messages)
        return AIMessage(content=self.answer)

    async def astream(
        self,
        messages: Sequence[BaseMessage],
        config: RunnableConfig | None = None,
    ) -> AsyncIterator[AIMessageChunk]:
        del config
        self.messages = list(messages)
        yield AIMessageChunk(content=self.answer)


def test_independent_product_and_order_tasks_run_in_parallel() -> None:
    calls: list[str] = []
    ready = asyncio.Event()
    synthesis = SynthesisClient("耳机推荐和历史订单已经汇总。")
    clients = {
        AgentName.PRODUCT: ParallelClient("找到耳机", calls, "product", ready),
        AgentName.ORDER: ParallelClient("找到充电器订单", calls, "order", ready),
    }
    runtime = MultiAgentRuntime(
        model_client=clients[AgentName.PRODUCT],
        query_preprocessor=FixedPreprocessor(
            IntentName.PRODUCT_INQUIRY,
            [IntentName.ORDER_INQUIRY],
        ),
        agent_clients=clients,
        synthesis_client=synthesis,
    )

    result = asyncio.run(runtime.ainvoke([HumanMessage(content="复合问题")]))

    assert set(calls) == {"product", "order"}
    assert result.agent_plan is not None
    assert result.agent_plan.execution_mode == "parallel"
    assert len(result.task_results) == 2
    assert result.reply.text == "耳机推荐和历史订单已经汇总。"
    assert result.model_calls == 3
    assert "找到耳机" in str(synthesis.messages[0].content)
    assert "找到充电器订单" in str(synthesis.messages[0].content)


def test_after_sales_task_waits_for_order_task() -> None:
    calls: list[str] = []
    synthesis = SynthesisClient("订单和售后条件已经汇总。")
    clients = {
        AgentName.ORDER: DirectClient("订单状态为待发货", calls, "order"),
        AgentName.AFTER_SALES: DirectClient("可以申请退款", calls, "after_sales"),
    }
    runtime = MultiAgentRuntime(
        model_client=clients[AgentName.ORDER],
        query_preprocessor=FixedPreprocessor(
            IntentName.ORDER_INQUIRY,
            [IntentName.AFTER_SALE],
        ),
        agent_clients=clients,
        synthesis_client=synthesis,
    )

    result = asyncio.run(runtime.ainvoke([HumanMessage(content="订单退款问题")]))

    assert calls == ["order", "after_sales"]
    assert result.agent_plan is not None
    assert result.agent_plan.execution_mode == "sequential"
    assert result.agent_plan.tasks[1].depends_on == [result.agent_plan.tasks[0].id]
