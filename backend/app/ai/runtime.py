"""使用 LangGraph 编排模型与商品工具的客服运行时。"""

from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
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

        response = await self._model_client.ainvoke(
            [SystemMessage(content=self._system_prompt), *state["messages"]],
            config=config,
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

        result = await self._tool_node.ainvoke(state, config=config)
        last_message = state["messages"][-1]
        return {
            "messages": result["messages"],
            "tool_rounds": state.get("tool_rounds", 0) + 1,
            "tool_calls": state.get("tool_calls", 0)
            + self._count_requested_tools(last_message),
        }

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
