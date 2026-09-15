"""会话生命周期、消息保存和 LangGraph 客服调用规则。"""

import asyncio
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.ai.model_client import ModelClientError
from app.ai.runtime import (
    RuntimeErrorBase,
    RuntimeResult,
    create_customer_service_runtime,
)
from app.modules.conversations.repository import ConversationRepository
from app.modules.conversations.schemas import (
    BuyerId,
    ChatRequest,
    ChatResponse,
    ChatRunStats,
    Conversation,
    ConversationMessage,
    ConversationMode,
    MessageRole,
)


class RuntimeProtocol(Protocol):
    """会话服务实际依赖的最小客服运行时接口。"""

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        config: dict[str, object] | None = None,
    ) -> RuntimeResult: ...


class ConversationServiceError(RuntimeError):
    """会话业务错误的基类。"""


class ConversationNotFoundError(ConversationServiceError):
    """请求的会话不存在。"""


class ConversationAccessError(ConversationServiceError):
    """会话不属于当前买家。"""


class ConversationUnavailableError(ConversationServiceError):
    """会话当前不能继续由 AI 回复。"""


class AssistantReplyError(ConversationServiceError):
    """模型运行失败或没有产生有效文本回复。"""


def utc_now() -> datetime:
    """返回带 UTC 时区的当前时间。"""

    return datetime.now(UTC)


class ConversationService:
    """创建和继续会话，并在成功回复后原子保存两条消息。"""

    def __init__(
        self,
        repository: ConversationRepository | None = None,
        runtime: RuntimeProtocol | None = None,
        runtime_factory: Callable[
            [], RuntimeProtocol
        ] = create_customer_service_runtime,
        id_factory: Callable[[], UUID] = uuid4,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.repository = repository or ConversationRepository()
        self._runtime = runtime
        self._runtime_factory = runtime_factory
        self._id_factory = id_factory
        self._clock = clock
        self._conversation_locks: dict[UUID, asyncio.Lock] = {}

    async def send_message(self, request: ChatRequest) -> ChatResponse:
        """创建或继续会话，调用客服图，并在成功后保存消息。"""

        conversation_id = request.conversation_id or self._id_factory()
        lock = self._conversation_locks.setdefault(conversation_id, asyncio.Lock())

        async with lock:
            existing = self.repository.get(conversation_id)
            conversation = self._resolve_conversation(
                existing=existing,
                conversation_id=conversation_id,
                buyer_id=request.buyer_id,
                first_message=request.message,
                allow_create=request.conversation_id is None,
            )
            user_message = ConversationMessage(
                id=self._id_factory(),
                role=MessageRole.USER,
                content=request.message,
                created_at=max(self._clock(), conversation.updated_at),
            )
            model_messages = self._to_model_messages(conversation.messages)
            model_messages.append(
                HumanMessage(
                    content=user_message.content,
                    id=str(user_message.id),
                )
            )

            try:
                result = await self._get_runtime().ainvoke(
                    model_messages,
                    config={
                        "configurable": {"thread_id": str(conversation_id)},
                        "tags": ["customer-service", str(request.buyer_id)],
                    },
                )
            except (ModelClientError, RuntimeErrorBase) as exc:
                raise AssistantReplyError("AI 客服暂时无法生成回复") from exc

            reply_text = result.reply.text.strip()
            if not reply_text:
                raise AssistantReplyError("AI 客服没有返回有效的文本回复")

            assistant_created_at = max(self._clock(), user_message.created_at)
            assistant_message = ConversationMessage(
                id=self._id_factory(),
                role=MessageRole.ASSISTANT,
                content=reply_text,
                created_at=assistant_created_at,
            )
            updated = Conversation(
                id=conversation.id,
                buyer_id=conversation.buyer_id,
                title=conversation.title,
                mode=conversation.mode,
                messages=[
                    *conversation.messages,
                    user_message,
                    assistant_message,
                ],
                created_at=conversation.created_at,
                updated_at=assistant_message.created_at,
            )

            if existing is None:
                self.repository.add(updated)
            else:
                self.repository.update(updated)

            return ChatResponse(
                conversation_id=updated.id,
                mode=updated.mode,
                user_message=user_message,
                assistant_message=assistant_message,
                run=ChatRunStats(
                    model_calls=result.model_calls,
                    tool_rounds=result.tool_rounds,
                    tool_calls=result.tool_calls,
                ),
            )

    def get_conversation(
        self,
        conversation_id: UUID,
        buyer_id: BuyerId,
    ) -> Conversation:
        """读取属于指定买家的会话。"""

        conversation = self.repository.get(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(f"未找到会话：{conversation_id}")
        self._ensure_buyer_access(conversation, buyer_id)
        return conversation

    def list_conversations(self, buyer_id: BuyerId) -> list[Conversation]:
        """返回指定买家的全部会话，最近更新的排在前面。"""

        return self.repository.list_by_buyer(buyer_id)

    def _get_runtime(self) -> RuntimeProtocol:
        """首次发送消息时才创建真实模型运行时。"""

        if self._runtime is None:
            self._runtime = self._runtime_factory()
        return self._runtime

    def _resolve_conversation(
        self,
        existing: Conversation | None,
        conversation_id: UUID,
        buyer_id: BuyerId,
        first_message: str,
        allow_create: bool,
    ) -> Conversation:
        """读取已有会话，或为第一条消息创建尚未保存的会话。"""

        if existing is None:
            if not allow_create:
                raise ConversationNotFoundError(f"未找到会话：{conversation_id}")
            created_at = self._clock()
            return Conversation(
                id=conversation_id,
                buyer_id=buyer_id,
                title=self._make_title(first_message),
                created_at=created_at,
                updated_at=created_at,
            )

        self._ensure_buyer_access(existing, buyer_id)
        if existing.mode is not ConversationMode.AI:
            raise ConversationUnavailableError(
                f"当前会话状态不能由 AI 继续回复：{existing.mode.value}"
            )
        return existing

    @staticmethod
    def _ensure_buyer_access(
        conversation: Conversation,
        buyer_id: BuyerId,
    ) -> None:
        """阻止买家读取或继续其他买家的会话。"""

        if conversation.buyer_id != buyer_id:
            raise ConversationAccessError("无权访问其他买家的会话")

    @staticmethod
    def _to_model_messages(
        messages: Sequence[ConversationMessage],
    ) -> list[BaseMessage]:
        """把已保存的业务消息转换为 LangChain 对话历史。"""

        result: list[BaseMessage] = []
        for message in messages:
            if message.role is MessageRole.USER:
                result.append(
                    HumanMessage(content=message.content, id=str(message.id))
                )
            elif message.role in {MessageRole.ASSISTANT, MessageRole.STAFF}:
                result.append(AIMessage(content=message.content, id=str(message.id)))
        return result

    @staticmethod
    def _make_title(message: str) -> str:
        """从第一条用户消息生成简短会话标题。"""

        return message if len(message) <= 24 else f"{message[:23]}…"
