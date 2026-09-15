"""使用 LangGraph 编排模型与商品工具的客服运行时。"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.messages.utils import message_chunk_to_message
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.prebuilt import ToolNode

from app.ai.model_client import ModelClient, create_model_client


DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parent / "prompts" / "customer_service.md"
)


class CustomerServiceState(TypedDict):
    """在 LangGraph 节点之间传递的客服状态。"""

    messages: Annotated[list[AnyMessage], add_messages]
    model_calls: int
    tool_rounds: int
    tool_calls: int


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    """一次客服运行的最终回复和可观测信息。"""

    reply: AIMessage
    messages: tuple[AnyMessage, ...]
    model_calls: int
    tool_rounds: int
    tool_calls: int


@dataclass(frozen=True, slots=True)
class RuntimeStreamEvent:
    """客服运行过程中发送给上层的单个流式事件。"""

    type: Literal["model_start", "delta", "tool_start", "tool_end", "complete"]
    text: str = ""
    tool_calls: int = 0
    result: RuntimeResult | None = None


StreamEventCallback = Callable[[RuntimeStreamEvent], Awaitable[None]]
STREAM_CALLBACK_KEY = "customer_service_stream_callback"


class RuntimeErrorBase(RuntimeError):
    """客服运行时错误的基类。"""


class RuntimeConfigurationError(RuntimeErrorBase):
    """客服提示词或运行参数配置错误。"""


class RuntimeInputError(RuntimeErrorBase):
    """传入的对话消息不符合客服运行要求。"""


class InvalidToolCallError(RuntimeErrorBase):
    """模型生成了无法解析的工具调用。"""


class RuntimeLoopLimitError(RuntimeErrorBase):
    """模型连续请求工具并超过了安全轮数。"""


@lru_cache(maxsize=1)
def load_customer_service_prompt() -> str:
    """读取并缓存客服系统提示词。"""

    try:
        prompt = DEFAULT_PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeConfigurationError("无法读取客服系统提示词") from exc

    if not prompt:
        raise RuntimeConfigurationError("客服系统提示词不能为空")
    return prompt


class CustomerServiceRuntime:
    """运行模型节点和商品工具节点组成的 LangGraph 状态图。"""

    def __init__(
        self,
        model_client: ModelClient,
        system_prompt: str | None = None,
        max_tool_rounds: int = 4,
    ) -> None:
        if max_tool_rounds < 1:
            raise RuntimeConfigurationError("max_tool_rounds 必须大于或等于 1")

        prompt = system_prompt.strip() if system_prompt is not None else None
        if system_prompt is not None and not prompt:
            raise RuntimeConfigurationError("客服系统提示词不能为空")

        self._model_client = model_client
        self._system_prompt = prompt or load_customer_service_prompt()
        self._max_tool_rounds = max_tool_rounds
        self._tool_node = ToolNode(
            list(model_client.tools),
            handle_tool_errors=self._format_tool_error,
        )
        self._graph = self._build_graph()

    @property
    def graph(self) -> Any:
        """返回已编译的 LangGraph，供调试和后续扩展节点。"""

        return self._graph

    @property
    def system_prompt(self) -> str:
        """返回当前运行时使用的系统提示词。"""

        return self._system_prompt

    def invoke(
        self,
        messages: Sequence[BaseMessage],
        config: RunnableConfig | None = None,
    ) -> RuntimeResult:
        """同步运行客服图，直到模型生成不含工具调用的最终回复。"""

        prepared_messages = self._validate_messages(messages)
        try:
            state = self._graph.invoke(
                self._initial_state(prepared_messages),
                config=self._graph_config(config),
            )
        except GraphRecursionError as exc:
            raise RuntimeLoopLimitError("客服运行图超过了最大执行步数") from exc
        return self._build_result(state)

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        config: RunnableConfig | None = None,
    ) -> RuntimeResult:
        """异步运行客服图，直到模型生成不含工具调用的最终回复。"""

        prepared_messages = self._validate_messages(messages)
        try:
            state = await self._graph.ainvoke(
                self._initial_state(prepared_messages),
                config=self._graph_config(config),
            )
        except GraphRecursionError as exc:
            raise RuntimeLoopLimitError("客服运行图超过了最大执行步数") from exc
        return self._build_result(state)

    async def astream(
        self,
        messages: Sequence[BaseMessage],
        config: RunnableConfig | None = None,
    ) -> AsyncIterator[RuntimeStreamEvent]:
        """运行客服图，并在模型或工具产生进度时立即返回事件。"""

        event_queue: asyncio.Queue[RuntimeStreamEvent | BaseException | None]
        event_queue = asyncio.Queue()

        async def publish(event: RuntimeStreamEvent) -> None:
            await event_queue.put(event)

        stream_config = self._with_stream_callback(config, publish)

        async def run_graph() -> None:
            try:
                result = await self.ainvoke(messages, config=stream_config)
                complete_event = RuntimeStreamEvent(
                    type="complete",
                    result=result,
                )
                await event_queue.put(complete_event)
            except BaseException as error:
                await event_queue.put(error)
            finally:
                await event_queue.put(None)

        graph_task = asyncio.create_task(run_graph())

        try:
            while True:
                event = await event_queue.get()
                if event is None:
                    break
                if isinstance(event, BaseException):
                    raise event
                yield event
        finally:
            if not graph_task.done():
                graph_task.cancel()
            with suppress(asyncio.CancelledError):
                await graph_task

    def _build_graph(self) -> Any:
        """创建“模型—工具—模型”的状态图。"""

        builder = StateGraph(CustomerServiceState)
        builder.add_node(
            "model",
            RunnableLambda(self._call_model, afunc=self._acall_model),
        )
        builder.add_node(
            "tools",
            RunnableLambda(self._call_tools, afunc=self._acall_tools),
        )
        builder.add_edge(START, "model")
        builder.add_conditional_edges(
            "model",
            self._route_after_model,
            {"tools": "tools", "end": END},
        )
        builder.add_edge("tools", "model")
        return builder.compile()

    def _call_model(
        self,
        state: CustomerServiceState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        """同步执行模型节点。"""

        response = self._model_client.invoke(
            [SystemMessage(content=self._system_prompt), *state["messages"]],
            config=config,
        )
        self._validate_model_response(response)
        return {
            "messages": [response],
            "model_calls": state.get("model_calls", 0) + 1,
        }

    async def _acall_model(
        self,
        state: CustomerServiceState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        """异步执行模型节点。"""

        messages = [SystemMessage(content=self._system_prompt), *state["messages"]]
        callback = self._get_stream_callback(config)

        if callback is None:
            response = await self._model_client.ainvoke(
                messages,
                config=config,
            )
        else:
            response = await self._stream_model_response(
                messages,
                config,
                callback,
            )
        self._validate_model_response(response)
        return {
            "messages": [response],
            "model_calls": state.get("model_calls", 0) + 1,
        }

    def _call_tools(
        self,
        state: CustomerServiceState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        """同步执行当前 AI 消息请求的全部商品工具。"""

        result = self._tool_node.invoke(state, config=config)
        last_message = state["messages"][-1]
        return {
            "messages": result["messages"],
            "tool_rounds": state.get("tool_rounds", 0) + 1,
            "tool_calls": state.get("tool_calls", 0)
            + self._count_requested_tools(last_message),
        }

    async def _acall_tools(
        self,
        state: CustomerServiceState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        """异步执行当前 AI 消息请求的全部商品工具。"""

        last_message = state["messages"][-1]
        requested_tool_count = self._count_requested_tools(last_message)
        callback = self._get_stream_callback(config)

        if callback is not None:
            await callback(
                RuntimeStreamEvent(
                    type="tool_start",
                    tool_calls=requested_tool_count,
                )
            )

        result = await self._tool_node.ainvoke(state, config=config)

        if callback is not None:
            await callback(
                RuntimeStreamEvent(
                    type="tool_end",
                    tool_calls=requested_tool_count,
                )
            )

        return {
            "messages": result["messages"],
            "tool_rounds": state.get("tool_rounds", 0) + 1,
            "tool_calls": state.get("tool_calls", 0)
            + requested_tool_count,
        }

    async def _stream_model_response(
        self,
        messages: Sequence[BaseMessage],
        config: RunnableConfig,
        callback: StreamEventCallback,
    ) -> AIMessage:
        """读取模型消息片段，同时拼接出供 LangGraph 使用的完整消息。"""

        await callback(RuntimeStreamEvent(type="model_start"))
        combined_chunk: AIMessageChunk | None = None

        async for chunk in self._model_client.astream(messages, config=config):
            if combined_chunk is None:
                combined_chunk = chunk
            else:
                combined_chunk = combined_chunk + chunk

            text = chunk.text
            if text:
                await callback(RuntimeStreamEvent(type="delta", text=text))

        if combined_chunk is None:
            raise RuntimeErrorBase("模型流式调用没有返回任何消息")

        response = message_chunk_to_message(combined_chunk)
        if not isinstance(response, AIMessage):
            raise RuntimeErrorBase("模型流式调用没有生成 AI 消息")
        return response

    def _route_after_model(
        self,
        state: CustomerServiceState,
    ) -> Literal["tools", "end"]:
        """根据模型是否请求工具选择下一条边。"""

        last_message = state["messages"][-1]
        if not isinstance(last_message, AIMessage):
            raise RuntimeErrorBase("模型节点没有生成 AI 消息")
        if not last_message.tool_calls:
            return "end"
        if state.get("tool_rounds", 0) >= self._max_tool_rounds:
            raise RuntimeLoopLimitError(
                f"模型连续调用工具超过 {self._max_tool_rounds} 轮"
            )
        return "tools"

    def _validate_messages(
        self,
        messages: Sequence[BaseMessage],
    ) -> list[BaseMessage]:
        """确认输入是一段以用户消息结尾的对话。"""

        prepared = list(messages)
        if not prepared:
            raise RuntimeInputError("对话消息不能为空")
        if not all(isinstance(message, BaseMessage) for message in prepared):
            raise RuntimeInputError("对话中包含无法识别的消息")
        if any(isinstance(message, SystemMessage) for message in prepared):
            raise RuntimeInputError("系统提示词只能由客服运行时设置")
        if not isinstance(prepared[-1], HumanMessage):
            raise RuntimeInputError("对话必须以最新的用户消息结尾")
        return prepared

    @staticmethod
    def _validate_model_response(response: AIMessage) -> None:
        """阻止无法解析的工具参数进入工具节点。"""

        if response.invalid_tool_calls:
            raise InvalidToolCallError("模型生成了无法解析的工具调用参数")

    @staticmethod
    def _count_requested_tools(message: AnyMessage) -> int:
        """统计当前模型消息请求的工具数量。"""

        return len(message.tool_calls) if isinstance(message, AIMessage) else 0

    @staticmethod
    def _format_tool_error(_error: Exception) -> str:
        """向模型返回固定错误，避免泄露内部异常和数据路径。"""

        return (
            '{"success":false,"error":{"code":"TOOL_EXECUTION_ERROR",'
            '"message":"商品工具执行失败，请检查参数后重试"}}'
        )

    @staticmethod
    def _initial_state(messages: list[BaseMessage]) -> CustomerServiceState:
        """创建每次运行的初始图状态。"""

        return {
            "messages": list(messages),
            "model_calls": 0,
            "tool_rounds": 0,
            "tool_calls": 0,
        }

    def _graph_config(
        self,
        config: RunnableConfig | None,
    ) -> RunnableConfig:
        """设置与工具轮数相匹配的 LangGraph 执行上限。"""

        graph_config: RunnableConfig = dict(config or {})
        graph_config["recursion_limit"] = self._max_tool_rounds * 2 + 4
        return graph_config

    @staticmethod
    def _with_stream_callback(
        config: RunnableConfig | None,
        callback: StreamEventCallback,
    ) -> RunnableConfig:
        """复制运行配置并加入仅供本次请求使用的事件回调。"""

        stream_config: RunnableConfig = dict(config or {})
        configurable = dict(stream_config.get("configurable") or {})
        configurable[STREAM_CALLBACK_KEY] = callback
        stream_config["configurable"] = configurable
        return stream_config

    @staticmethod
    def _get_stream_callback(
        config: RunnableConfig,
    ) -> StreamEventCallback | None:
        """从运行配置中读取流式事件回调。"""

        configurable = config.get("configurable") or {}
        callback = configurable.get(STREAM_CALLBACK_KEY)
        return callback if callable(callback) else None

    @staticmethod
    def _build_result(state: CustomerServiceState) -> RuntimeResult:
        """从最终图状态提取回复和调用统计。"""

        messages = tuple(state["messages"])
        if not messages or not isinstance(messages[-1], AIMessage):
            raise RuntimeErrorBase("客服运行图没有生成最终回复")

        reply = messages[-1]
        if reply.tool_calls:
            raise RuntimeErrorBase("客服运行图结束时仍有未执行的工具调用")

        return RuntimeResult(
            reply=reply,
            messages=messages,
            model_calls=state.get("model_calls", 0),
            tool_rounds=state.get("tool_rounds", 0),
            tool_calls=state.get("tool_calls", 0),
        )


def create_customer_service_runtime() -> CustomerServiceRuntime:
    """使用当前服务端模型配置创建客服运行时。"""

    return CustomerServiceRuntime(create_model_client())
