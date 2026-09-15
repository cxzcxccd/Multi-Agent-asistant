"""模型客户端配置、工具绑定和调用边界测试。"""

import asyncio
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

import pytest
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
)
from pydantic import SecretStr

import app.ai.model_client as model_client_module
from app.ai.model_client import (
    ModelClient,
    ModelConfigurationError,
    ModelInvocationError,
    UnexpectedModelResponseError,
    create_model_client,
)
from app.core.config import Settings


class FakeBoundModel:
    """记录调用参数并返回指定结果的模型替身。"""

    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response if response is not None else AIMessage(content="好的")
        self.error = error
        self.calls: list[tuple[str, list[BaseMessage], Any]] = []

    def invoke(self, messages: list[BaseMessage], config: Any = None) -> Any:
        self.calls.append(("invoke", messages, config))
        if self.error is not None:
            raise self.error
        return self.response

    async def ainvoke(self, messages: list[BaseMessage], config: Any = None) -> Any:
        self.calls.append(("ainvoke", messages, config))
        if self.error is not None:
            raise self.error
        return self.response

    def stream(
        self, messages: list[BaseMessage], config: Any = None
    ) -> Iterator[Any]:
        self.calls.append(("stream", messages, config))
        if self.error is not None:
            raise self.error
        yield AIMessageChunk(content="好")
        yield AIMessageChunk(content="的")

    async def astream(
        self, messages: list[BaseMessage], config: Any = None
    ) -> AsyncIterator[Any]:
        self.calls.append(("astream", messages, config))
        if self.error is not None:
            raise self.error
        yield AIMessageChunk(content="好")
        yield AIMessageChunk(content="的")


class FakeChatModel:
    """记录绑定工具并提供可调用模型替身。"""

    def __init__(self, bound_model: FakeBoundModel | None = None) -> None:
        self.bound_model = bound_model or FakeBoundModel()
        self.bound_tools: list[Any] = []

    def bind_tools(self, tools: Sequence[Any]) -> FakeBoundModel:
        self.bound_tools = list(tools)
        return self.bound_model


def make_settings(**overrides: Any) -> Settings:
    """创建包含最小有效模型配置的测试设置。"""

    values: dict[str, Any] = {
        "model_provider": "openai",
        "model_name": "test-model",
        "model_api_key": SecretStr("test-secret"),
        "model_base_url": "",
        "model_temperature": 0.1,
        "model_timeout_seconds": 30,
        "model_max_retries": 2,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        ({"model_name": ""}, "MODEL_NAME"),
        ({"model_api_key": None}, "MODEL_API_KEY"),
        ({"model_provider": "unknown"}, "MODEL_PROVIDER"),
        (
            {"model_provider": "openai-compatible", "model_base_url": ""},
            "MODEL_BASE_URL",
        ),
    ],
)
def test_create_model_client_rejects_incomplete_configuration(
    overrides: dict[str, Any], expected_message: str
) -> None:
    with pytest.raises(ModelConfigurationError, match=expected_message):
        create_model_client(make_settings(**overrides))


def test_create_model_client_builds_openai_model_and_binds_catalog_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    fake_model = FakeChatModel()

    def fake_chat_openai(**kwargs: Any) -> FakeChatModel:
        captured.update(kwargs)
        return fake_model

    monkeypatch.setattr(model_client_module, "ChatOpenAI", fake_chat_openai)

    client = create_model_client(make_settings())

    assert captured == {
        "model": "test-model",
        "api_key": "test-secret",
        "base_url": None,
        "temperature": 0.1,
        "timeout": 30.0,
        "max_retries": 2,
    }
    assert [tool.name for tool in client.tools] == ["search_products", "get_product"]
    assert fake_model.bound_tools == list(client.tools)


def test_create_model_client_passes_compatible_service_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_chat_openai(**kwargs: Any) -> FakeChatModel:
        captured.update(kwargs)
        return FakeChatModel()

    monkeypatch.setattr(model_client_module, "ChatOpenAI", fake_chat_openai)

    create_model_client(
        make_settings(
            model_provider="openai_compatible",
            model_base_url="https://model.example.com/v1",
        )
    )

    assert captured["base_url"] == "https://model.example.com/v1"


def test_model_client_can_skip_tool_binding() -> None:
    fake_model = FakeBoundModel()

    client = ModelClient(model=fake_model, tools=[])
    response = client.invoke([HumanMessage(content="你好")])

    assert response.content == "好的"
    assert client.tools == ()


def test_model_client_supports_sync_async_and_stream_calls() -> None:
    bound_model = FakeBoundModel()
    client = ModelClient(model=FakeChatModel(bound_model))
    messages = [HumanMessage(content="推荐一款耳机")]

    async def run_async_calls() -> tuple[AIMessage, list[AIMessageChunk]]:
        """在 pytest 插件之外运行并收集异步调用结果。"""

        response = await client.ainvoke(messages, config={"tags": ["async"]})
        chunks = [chunk async for chunk in client.astream(messages)]
        return response, chunks

    sync_response = client.invoke(messages, config={"tags": ["sync"]})
    sync_chunks = list(client.stream(messages))
    async_response, async_chunks = asyncio.run(run_async_calls())

    assert sync_response.content == "好的"
    assert async_response.content == "好的"
    assert "".join(str(chunk.content) for chunk in sync_chunks) == "好的"
    assert "".join(str(chunk.content) for chunk in async_chunks) == "好的"
    assert [call[0] for call in bound_model.calls] == [
        "invoke",
        "stream",
        "ainvoke",
        "astream",
    ]


def test_model_client_wraps_provider_errors_without_exposing_details() -> None:
    client = ModelClient(
        model=FakeBoundModel(error=RuntimeError("包含敏感信息的服务异常")),
        tools=[],
    )

    with pytest.raises(ModelInvocationError) as exc_info:
        client.invoke([HumanMessage(content="你好")])

    assert "包含敏感信息" not in str(exc_info.value)
    assert "模型调用失败" in str(exc_info.value)


def test_model_client_rejects_unexpected_response_type() -> None:
    client = ModelClient(model=FakeBoundModel(response="普通字符串"), tools=[])

    with pytest.raises(UnexpectedModelResponseError):
        client.invoke([HumanMessage(content="你好")])


def test_model_api_key_is_hidden_in_settings_representation() -> None:
    configured_settings = make_settings()

    assert "test-secret" not in repr(configured_settings)
    assert "**********" in repr(configured_settings)
