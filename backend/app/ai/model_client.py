"""创建并调用可绑定业务工具的聊天模型客户端。"""

from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from app.ai.tools.catalog import get_catalog_tools
from app.core.config import Settings, settings


class ModelClientError(RuntimeError):
    """模型客户端错误的基类。"""


class ModelConfigurationError(ModelClientError):
    """模型配置缺失或不受支持。"""


class ModelInvocationError(ModelClientError):
    """模型服务调用失败。"""


class UnexpectedModelResponseError(ModelClientError):
    """模型返回了当前聊天流程无法处理的数据类型。"""


class ModelClient:
    """封装聊天模型、商品工具绑定以及同步和异步调用。"""

    def __init__(
        self,
        model: Any,
        tools: Sequence[BaseTool] | None = None,
    ) -> None:
        self._model = model
        self._tools = tuple(get_catalog_tools() if tools is None else tools)
        self._runnable: Runnable[Any, Any] = (
            model.bind_tools(list(self._tools)) if self._tools else model
        )

    @property
    def model(self) -> Any:
        """返回尚未绑定工具的原始模型，便于后续扩展。"""

        return self._model

    @property
    def tools(self) -> tuple[BaseTool, ...]:
        """返回已经绑定的工具，防止调用方修改内部列表。"""

        return self._tools

    def invoke(
        self,
        messages: Sequence[BaseMessage],
        config: RunnableConfig | None = None,
    ) -> AIMessage:
        """同步调用模型并返回一条完整的 AI 消息。"""

        try:
            response = self._runnable.invoke(list(messages), config=config)
        except Exception as exc:
            raise ModelInvocationError(
                "模型调用失败，请检查模型配置、网络连接和服务状态"
            ) from exc
        return self._require_ai_message(response)

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        config: RunnableConfig | None = None,
    ) -> AIMessage:
        """异步调用模型并返回一条完整的 AI 消息。"""

        try:
            response = await self._runnable.ainvoke(list(messages), config=config)
        except Exception as exc:
            raise ModelInvocationError(
                "模型调用失败，请检查模型配置、网络连接和服务状态"
            ) from exc
        return self._require_ai_message(response)

    def stream(
        self,
        messages: Sequence[BaseMessage],
        config: RunnableConfig | None = None,
    ) -> Iterator[AIMessageChunk]:
        """同步流式返回模型生成的消息片段。"""

        try:
            for chunk in self._runnable.stream(list(messages), config=config):
                yield self._require_ai_message_chunk(chunk)
        except ModelClientError:
            raise
        except Exception as exc:
            raise ModelInvocationError(
                "模型流式调用失败，请检查模型配置、网络连接和服务状态"
            ) from exc

    async def astream(
        self,
        messages: Sequence[BaseMessage],
        config: RunnableConfig | None = None,
    ) -> AsyncIterator[AIMessageChunk]:
        """异步流式返回模型生成的消息片段。"""

        try:
            async for chunk in self._runnable.astream(list(messages), config=config):
                yield self._require_ai_message_chunk(chunk)
        except ModelClientError:
            raise
        except Exception as exc:
            raise ModelInvocationError(
                "模型流式调用失败，请检查模型配置、网络连接和服务状态"
            ) from exc

    @staticmethod
    def _require_ai_message(response: Any) -> AIMessage:
        """确认普通调用返回的是 AI 消息。"""

        if not isinstance(response, AIMessage):
            raise UnexpectedModelResponseError("模型没有返回有效的 AI 消息")
        return response

    @staticmethod
    def _require_ai_message_chunk(response: Any) -> AIMessageChunk:
        """确认流式调用返回的是 AI 消息片段。"""

        if not isinstance(response, AIMessageChunk):
            raise UnexpectedModelResponseError("模型没有返回有效的 AI 消息片段")
        return response


def create_model_client(
    app_settings: Settings | None = None,
    tools: Sequence[BaseTool] | None = None,
) -> ModelClient:
    """根据服务端配置创建一个 OpenAI 或 OpenAI 兼容模型客户端。"""

    current_settings = app_settings or settings
    provider = current_settings.model_provider.strip().lower().replace("_", "-")

    if provider not in {"openai", "openai-compatible"}:
        raise ModelConfigurationError(
            "MODEL_PROVIDER 目前只支持 openai 或 openai-compatible"
        )

    model_name = current_settings.model_name.strip()
    if not model_name:
        raise ModelConfigurationError("缺少 MODEL_NAME，请先配置模型名称")

    api_key = (
        current_settings.model_api_key.get_secret_value().strip()
        if current_settings.model_api_key is not None
        else ""
    )
    if not api_key:
        raise ModelConfigurationError("缺少 MODEL_API_KEY，请先配置服务端模型密钥")

    base_url = current_settings.model_base_url.strip()
    if provider == "openai-compatible" and not base_url:
        raise ModelConfigurationError(
            "使用 openai-compatible 时必须配置 MODEL_BASE_URL"
        )

    model = ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url or None,
        temperature=current_settings.model_temperature,
        timeout=current_settings.model_timeout_seconds,
        max_retries=current_settings.model_max_retries,
    )
    return ModelClient(model=model, tools=tools)
