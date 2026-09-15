"""会话、消息和聊天接口数据格式测试。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.modules.conversations.schemas import (
    ChatRequest,
    ChatResponse,
    ChatRunStats,
    Conversation,
    ConversationMessage,
    ConversationMode,
    MessageRole,
)


def make_message(
    role: MessageRole = MessageRole.USER,
    content: str = "推荐一款耳机",
    created_at: datetime | None = None,
) -> ConversationMessage:
    """创建一条有效的测试消息。"""

    return ConversationMessage(
        id=uuid4(),
        role=role,
        content=content,
        created_at=created_at or datetime.now(UTC),
    )


def test_chat_request_normalizes_text_and_parses_conversation_id() -> None:
    conversation_id = uuid4()

    request = ChatRequest(
        buyer_id="A",
        conversation_id=str(conversation_id),
        message="  推荐一款三百元以内的耳机  ",
    )

    assert request.buyer_id == "A"
    assert request.conversation_id == conversation_id
    assert request.message == "推荐一款三百元以内的耳机"


def test_chat_request_allows_starting_a_new_conversation() -> None:
    request = ChatRequest(buyer_id="B", message="你好")

    assert request.conversation_id is None


@pytest.mark.parametrize(
    "values",
    [
        {"buyer_id": "C", "message": "你好"},
        {"buyer_id": "A", "message": "   "},
        {"buyer_id": "A", "message": "x" * 4001},
        {"buyer_id": "A", "message": "你好", "unknown": True},
    ],
)
def test_chat_request_rejects_invalid_input(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(values)


def test_message_requires_timezone_and_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ConversationMessage(
            id=uuid4(),
            role="user",
            content="你好",
            created_at=datetime.now(),
        )

    with pytest.raises(ValidationError):
        ConversationMessage.model_validate(
            {
                "id": str(uuid4()),
                "role": "user",
                "content": "你好",
                "created_at": datetime.now(UTC).isoformat(),
                "streaming": False,
            }
        )


def test_conversation_accepts_an_ordered_message_timeline() -> None:
    created_at = datetime.now(UTC)
    user_message = make_message(created_at=created_at)
    assistant_message = make_message(
        role=MessageRole.ASSISTANT,
        content="为你推荐 AirBeat Pro。",
        created_at=created_at + timedelta(seconds=1),
    )

    conversation = Conversation(
        id=uuid4(),
        buyer_id="A",
        title="耳机推荐",
        messages=[user_message, assistant_message],
        created_at=created_at,
        updated_at=assistant_message.created_at,
    )

    assert conversation.mode is ConversationMode.AI
    assert conversation.messages[-1].role is MessageRole.ASSISTANT
    assert {role.value for role in MessageRole} == {
        "user",
        "assistant",
        "staff",
        "system",
    }


@pytest.mark.parametrize(
    "timeline_error",
    ["updated_before_created", "messages_out_of_order", "message_outside_range"],
)
def test_conversation_rejects_invalid_timeline(timeline_error: str) -> None:
    created_at = datetime.now(UTC)
    first = make_message(created_at=created_at)
    second = make_message(
        role=MessageRole.ASSISTANT,
        created_at=created_at + timedelta(seconds=1),
    )
    updated_at = second.created_at
    messages = [first, second]

    if timeline_error == "updated_before_created":
        updated_at = created_at - timedelta(seconds=1)
        messages = []
    elif timeline_error == "messages_out_of_order":
        messages = [second, first]
    else:
        updated_at = created_at

    with pytest.raises(ValidationError):
        Conversation(
            id=uuid4(),
            buyer_id="A",
            title="时间错误测试",
            messages=messages,
            created_at=created_at,
            updated_at=updated_at,
        )


def test_chat_run_stats_reject_impossible_tool_counts() -> None:
    with pytest.raises(ValidationError, match="工具调用次数不能少于工具轮次"):
        ChatRunStats(model_calls=2, tool_rounds=2, tool_calls=1)


def test_chat_response_checks_roles_and_serializes_as_json() -> None:
    created_at = datetime.now(UTC)
    conversation_id = uuid4()
    user_message = make_message(created_at=created_at)
    assistant_message = make_message(
        role=MessageRole.ASSISTANT,
        content="推荐 AirBeat Pro。",
        created_at=created_at + timedelta(seconds=1),
    )

    response = ChatResponse(
        conversation_id=conversation_id,
        mode=ConversationMode.AI,
        user_message=user_message,
        assistant_message=assistant_message,
        run=ChatRunStats(model_calls=2, tool_rounds=1, tool_calls=1),
    )
    serialized = response.model_dump(mode="json")

    assert serialized["conversation_id"] == str(conversation_id)
    assert serialized["assistant_message"]["role"] == "assistant"
    assert UUID(serialized["assistant_message"]["id"]) == assistant_message.id


def test_chat_response_rejects_reversed_roles() -> None:
    created_at = datetime.now(UTC)

    with pytest.raises(ValidationError):
        ChatResponse(
            conversation_id=uuid4(),
            mode="ai",
            user_message=make_message(
                role=MessageRole.ASSISTANT,
                created_at=created_at,
            ),
            assistant_message=make_message(
                role=MessageRole.ASSISTANT,
                created_at=created_at + timedelta(seconds=1),
            ),
            run=ChatRunStats(model_calls=1, tool_rounds=0, tool_calls=0),
        )


def test_chat_response_rejects_reply_before_user_message() -> None:
    created_at = datetime.now(UTC)

    with pytest.raises(ValidationError, match="AI 回复时间不能早于用户消息"):
        ChatResponse(
            conversation_id=uuid4(),
            mode="ai",
            user_message=make_message(created_at=created_at),
            assistant_message=make_message(
                role=MessageRole.ASSISTANT,
                created_at=created_at - timedelta(seconds=1),
            ),
            run=ChatRunStats(model_calls=1, tool_rounds=0, tool_calls=0),
        )
