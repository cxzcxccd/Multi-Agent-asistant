"""会话服务的消息保存、权限、模型调用和并发处理测试。"""

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.ai.model_client import ModelInvocationError
from app.ai.runtime import RuntimeResult, RuntimeStreamEvent
from app.modules.conversations.repository import ConversationRepository
from app.modules.conversations.schemas import (
    ChatRequest,
    Conversation,
    BuyerId,
    ConversationMessage,
    ConversationMode,
    MessageRole,
)
from app.modules.conversations.service import (
    AssistantReplyError,
    ConversationAccessError,
    ConversationNotFoundError,
    ConversationService,
    ConversationUnavailableError,
    ConversationStreamEvent,
)


class StepClock:
    """每次调用都向后推进一秒的测试时钟。"""

    def __init__(self) -> None:
        self.current = datetime(2026, 9, 15, 8, tzinfo=UTC)

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(seconds=1)
        return value


class FakeRuntime:
    """返回预设结果或异常，并记录收到的对话历史。"""

    def __init__(
        self,
        replies: Sequence[str] = ("这是 AI 回复。",),
        error: Exception | None = None,
    ) -> None:
        self.replies = deque(replies)
        self.error = error
        self.calls: list[tuple[list[BaseMessage], dict[str, object] | None]] = []

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        config: dict[str, object] | None = None,
    ) -> RuntimeResult:
        self.calls.append((list(messages), config))
        if self.error is not None:
            raise self.error
        reply = AIMessage(content=self.replies.popleft())
        return RuntimeResult(
            reply=reply,
            messages=tuple([*messages, reply]),
            model_calls=2,
            tool_rounds=1,
            tool_calls=1,
        )


class EchoRuntime:
    """用于验证同一会话不会并发调用模型的运行时替身。"""

    def __init__(self) -> None:
        self.active_calls = 0
        self.max_active_calls = 0
        self.calls: list[list[BaseMessage]] = []

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        config: dict[str, object] | None = None,
    ) -> RuntimeResult:
        self.calls.append(list(messages))
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        await asyncio.sleep(0.01)
        reply = AIMessage(content=f"回复：{messages[-1].content}")
        self.active_calls -= 1
        return RuntimeResult(
            reply=reply,
            messages=tuple([*messages, reply]),
            model_calls=1,
            tool_rounds=0,
            tool_calls=0,
        )


class FailingStreamRuntime:
    """先返回一个文本片段，再模拟模型连接失败。"""

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        config: dict[str, object] | None = None,
    ) -> RuntimeResult:
        raise ModelInvocationError("模型不可用")

    async def astream(
        self,
        messages: Sequence[BaseMessage],
        config: dict[str, object] | None = None,
    ) -> AsyncIterator[RuntimeStreamEvent]:
        yield RuntimeStreamEvent(type="model_start")
        yield RuntimeStreamEvent(type="delta", text="未完成")
        raise ModelInvocationError("模型不可用")


def id_factory(values: Sequence[UUID]) -> Callable[[], UUID]:
    """按顺序生成测试指定的 UUID。"""

    iterator = iter(values)
    return lambda: next(iterator)


def make_conversation(
    conversation_id: UUID | None = None,
    buyer_id: BuyerId = "A",
    mode: ConversationMode = ConversationMode.AI,
    messages: list[ConversationMessage] | None = None,
) -> Conversation:
    """创建用于服务测试的会话。"""

    created_at = datetime(2026, 9, 15, 8, tzinfo=UTC)
    saved_messages = messages or []
    updated_at = saved_messages[-1].created_at if saved_messages else created_at
    return Conversation(
        id=conversation_id or uuid4(),
        buyer_id=buyer_id,
        title="商品咨询",
        mode=mode,
        messages=saved_messages,
        created_at=created_at,
        updated_at=updated_at,
    )


def test_service_creates_conversation_and_saves_two_messages() -> None:
    conversation_id, user_id, assistant_id = uuid4(), uuid4(), uuid4()
    repository = ConversationRepository()
    runtime = FakeRuntime(["推荐 AirBeat Pro，售价 299 元。"])
    service = ConversationService(
        repository=repository,
        runtime=runtime,
        id_factory=id_factory([conversation_id, user_id, assistant_id]),
        clock=StepClock(),
    )

    response = asyncio.run(
        service.send_message(
            ChatRequest(buyer_id="A", message="推荐三百元以内的耳机")
        )
    )

    saved = repository.get(conversation_id)
    assert saved is not None
    assert response.conversation_id == conversation_id
    assert response.user_message.id == user_id
    assert response.assistant_message.id == assistant_id
    assert [message.role for message in saved.messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert response.run.model_calls == 2
    assert response.run.tool_calls == 1
    assert runtime.calls[0][0][-1].content == "推荐三百元以内的耳机"
    config = runtime.calls[0][1]
    assert config is not None
    assert config["configurable"] == {
        "thread_id": str(conversation_id),
        "buyer_id": "A",
    }


def test_service_continues_conversation_with_relevant_history() -> None:
    base_time = datetime(2026, 9, 15, 8, tzinfo=UTC)
    conversation_id = uuid4()
    history = [
        ConversationMessage(
            id=uuid4(),
            role=MessageRole.SYSTEM,
            content="已记录系统事件",
            created_at=base_time,
        ),
        ConversationMessage(
            id=uuid4(),
            role=MessageRole.USER,
            content="想买耳机",
            created_at=base_time + timedelta(seconds=1),
        ),
        ConversationMessage(
            id=uuid4(),
            role=MessageRole.ASSISTANT,
            content="预算是多少？",
            created_at=base_time + timedelta(seconds=2),
        ),
        ConversationMessage(
            id=uuid4(),
            role=MessageRole.STAFF,
            content="也可以告诉我们使用场景。",
            created_at=base_time + timedelta(seconds=3),
        ),
    ]
    repository = ConversationRepository()
    repository.add(make_conversation(conversation_id, messages=history))
    runtime = FakeRuntime(["可以考虑 AirBeat Pro。"])
    service = ConversationService(
        repository=repository,
        runtime=runtime,
        id_factory=id_factory([uuid4(), uuid4()]),
        clock=StepClock(),
    )

    asyncio.run(
        service.send_message(
            ChatRequest(
                buyer_id="A",
                conversation_id=conversation_id,
                message="预算三百元",
            )
        )
    )

    model_history = runtime.calls[0][0]
    assert [type(message) for message in model_history] == [
        HumanMessage,
        AIMessage,
        AIMessage,
        HumanMessage,
    ]
    assert all(message.content != "已记录系统事件" for message in model_history)
    assert len(service.get_conversation(conversation_id, "A").messages) == 6


def test_service_rejects_unknown_or_other_buyer_conversation() -> None:
    conversation_id = uuid4()
    repository = ConversationRepository()
    repository.add(make_conversation(conversation_id, buyer_id="A"))
    runtime = FakeRuntime()
    service = ConversationService(repository=repository, runtime=runtime)

    async def run_checks() -> None:
        with pytest.raises(ConversationNotFoundError):
            await service.send_message(
                ChatRequest(
                    buyer_id="A",
                    conversation_id=uuid4(),
                    message="你好",
                )
            )
        with pytest.raises(ConversationAccessError):
            await service.send_message(
                ChatRequest(
                    buyer_id="B",
                    conversation_id=conversation_id,
                    message="你好",
                )
            )

    asyncio.run(run_checks())
    assert runtime.calls == []


@pytest.mark.parametrize(
    "mode",
    [ConversationMode.WAITING, ConversationMode.HUMAN, ConversationMode.CLOSED],
)
def test_service_rejects_conversations_not_handled_by_ai(
    mode: ConversationMode,
) -> None:
    conversation = make_conversation(mode=mode)
    repository = ConversationRepository()
    repository.add(conversation)
    runtime = FakeRuntime()
    service = ConversationService(repository=repository, runtime=runtime)

    with pytest.raises(ConversationUnavailableError):
        asyncio.run(
            service.send_message(
                ChatRequest(
                    buyer_id="A",
                    conversation_id=conversation.id,
                    message="继续咨询",
                )
            )
        )
    assert runtime.calls == []


def test_service_does_not_save_partial_message_when_runtime_fails() -> None:
    conversation = make_conversation()
    repository = ConversationRepository()
    repository.add(conversation)
    runtime = FakeRuntime(error=ModelInvocationError("模型不可用"))
    service = ConversationService(
        repository=repository,
        runtime=runtime,
        id_factory=id_factory([uuid4()]),
        clock=StepClock(),
    )

    with pytest.raises(AssistantReplyError):
        asyncio.run(
            service.send_message(
                ChatRequest(
                    buyer_id="A",
                    conversation_id=conversation.id,
                    message="推荐耳机",
                )
            )
        )

    saved = repository.get(conversation.id)
    assert saved is not None
    assert saved.messages == []


def test_service_rejects_empty_model_reply_without_saving() -> None:
    repository = ConversationRepository()
    runtime = FakeRuntime(["   "])
    service = ConversationService(
        repository=repository,
        runtime=runtime,
        id_factory=id_factory([uuid4(), uuid4()]),
        clock=StepClock(),
    )

    with pytest.raises(AssistantReplyError, match="有效的文本回复"):
        asyncio.run(service.send_message(ChatRequest(buyer_id="A", message="你好")))

    assert repository.count() == 0


def test_stream_failure_does_not_save_partial_messages() -> None:
    repository = ConversationRepository()
    service = ConversationService(
        repository=repository,
        runtime=FailingStreamRuntime(),
        id_factory=id_factory([uuid4(), uuid4(), uuid4()]),
        clock=StepClock(),
    )

    async def collect_events() -> list[ConversationStreamEvent]:
        events: list[ConversationStreamEvent] = []
        stream = service.stream_message(ChatRequest(buyer_id="A", message="你好"))
        async for event in stream:
            events.append(event)
        return events

    with pytest.raises(AssistantReplyError):
        asyncio.run(collect_events())

    assert repository.count() == 0


def test_service_lists_and_reads_only_owned_conversations() -> None:
    repository = ConversationRepository()
    owned = make_conversation(buyer_id="A")
    repository.add(owned)
    repository.add(make_conversation(buyer_id="B"))
    service = ConversationService(repository=repository, runtime=FakeRuntime())

    assert service.get_conversation(owned.id, "A").id == owned.id
    assert [item.id for item in service.list_conversations("A")] == [owned.id]
    with pytest.raises(ConversationAccessError):
        service.get_conversation(owned.id, "B")
    with pytest.raises(ConversationNotFoundError):
        service.get_conversation(uuid4(), "A")


def test_service_creates_runtime_lazily_and_only_once() -> None:
    runtime = FakeRuntime(["第一条回复", "第二条回复"])
    factory_calls = 0

    def make_runtime() -> FakeRuntime:
        nonlocal factory_calls
        factory_calls += 1
        return runtime

    service = ConversationService(
        runtime_factory=make_runtime,
        id_factory=id_factory([uuid4() for _ in range(6)]),
        clock=StepClock(),
    )

    assert factory_calls == 0
    asyncio.run(service.send_message(ChatRequest(buyer_id="A", message="第一条")))
    asyncio.run(service.send_message(ChatRequest(buyer_id="B", message="第二条")))
    assert factory_calls == 1


def test_service_serializes_concurrent_messages_for_same_conversation() -> None:
    conversation = make_conversation()
    repository = ConversationRepository()
    repository.add(conversation)
    runtime = EchoRuntime()
    service = ConversationService(
        repository=repository,
        runtime=runtime,
        id_factory=id_factory([uuid4() for _ in range(4)]),
        clock=StepClock(),
    )

    async def send_both() -> None:
        await asyncio.gather(
            service.send_message(
                ChatRequest(
                    buyer_id="A",
                    conversation_id=conversation.id,
                    message="第一条",
                )
            ),
            service.send_message(
                ChatRequest(
                    buyer_id="A",
                    conversation_id=conversation.id,
                    message="第二条",
                )
            ),
        )

    asyncio.run(send_both())

    saved = repository.get(conversation.id)
    assert saved is not None
    assert runtime.max_active_calls == 1
    assert [message.content for message in saved.messages[::2]] == ["第一条", "第二条"]
    assert [len(messages) for messages in runtime.calls] == [1, 3]
