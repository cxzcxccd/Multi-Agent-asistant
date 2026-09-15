"""会话、消息以及聊天接口的请求和响应数据格式。"""

from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

BuyerId = Literal["A", "B"]
MessageContent = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4000),
]
ConversationTitle = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]


class MessageRole(StrEnum):
    """会话中可以持久化的消息角色。"""

    USER = "user"
    ASSISTANT = "assistant"
    STAFF = "staff"
    SYSTEM = "system"


class ConversationMode(StrEnum):
    """会话当前由谁处理以及是否已经结束。"""

    AI = "ai"
    WAITING = "waiting"
    HUMAN = "human"
    CLOSED = "closed"


class ConversationMessage(BaseModel):
    """一条可保存并返回给前端的会话消息。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: UUID
    role: MessageRole
    content: MessageContent
    created_at: AwareDatetime


class Conversation(BaseModel):
    """包含消息历史和处理状态的完整会话快照。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: UUID
    buyer_id: BuyerId
    title: ConversationTitle
    mode: ConversationMode = ConversationMode.AI
    messages: list[ConversationMessage] = Field(default_factory=list)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        """确保会话及其消息时间按先后顺序排列。"""

        if self.updated_at < self.created_at:
            raise ValueError("会话更新时间不能早于创建时间")

        for previous, current in zip(self.messages, self.messages[1:]):
            if current.created_at < previous.created_at:
                raise ValueError("会话消息必须按创建时间顺序排列")

        if self.messages and self.messages[0].created_at < self.created_at:
            raise ValueError("消息创建时间不能早于会话创建时间")
        if self.messages and self.messages[-1].created_at > self.updated_at:
            raise ValueError("会话更新时间不能早于最后一条消息")
        return self


class ChatRequest(BaseModel):
    """发送一条用户消息时提交的数据。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    buyer_id: BuyerId
    conversation_id: UUID | None = None
    message: MessageContent


class ChatRunStats(BaseModel):
    """一次 LangGraph 客服运行的调用统计。"""

    model_config = ConfigDict(extra="forbid")

    model_calls: int = Field(ge=1)
    tool_rounds: int = Field(ge=0)
    tool_calls: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_tool_counts(self) -> Self:
        """每个工具轮次至少应包含一次工具调用。"""

        if self.tool_calls < self.tool_rounds:
            raise ValueError("工具调用次数不能少于工具轮次")
        return self


class ChatResponse(BaseModel):
    """保存用户消息并生成 AI 回复后的接口结果。"""

    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    mode: ConversationMode
    user_message: ConversationMessage
    assistant_message: ConversationMessage
    run: ChatRunStats

    @model_validator(mode="after")
    def validate_message_roles(self) -> Self:
        """确保请求消息和回复消息没有角色颠倒。"""

        if self.user_message.role is not MessageRole.USER:
            raise ValueError("user_message 必须是用户消息")
        if self.assistant_message.role is not MessageRole.ASSISTANT:
            raise ValueError("assistant_message 必须是 AI 客服消息")
        if self.assistant_message.created_at < self.user_message.created_at:
            raise ValueError("AI 回复时间不能早于用户消息")
        return self


class ChatStreamStart(BaseModel):
    """SSE 连接建立后首先返回的会话和消息编号。"""

    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    mode: ConversationMode
    user_message: ConversationMessage
    assistant_message_id: UUID


class ChatStreamStatus(BaseModel):
    """模型或商品工具当前执行到的阶段。"""

    model_config = ConfigDict(extra="forbid")

    phase: Literal["model", "tool"]
    state: Literal["started", "completed"]
    tool_calls: int = Field(default=0, ge=0)


class ChatStreamDelta(BaseModel):
    """模型新生成的一段回复文本。"""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)


class ChatStreamError(BaseModel):
    """SSE 已建立后返回的可处理错误。"""

    model_config = ConfigDict(extra="forbid")

    status: int = Field(ge=400, le=599)
    detail: str = Field(min_length=1)
