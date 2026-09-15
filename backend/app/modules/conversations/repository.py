"""线程安全的内存会话数据访问层。"""

from threading import RLock
from uuid import UUID

from app.modules.conversations.schemas import BuyerId, Conversation


class ConversationRepositoryError(RuntimeError):
    """内存会话仓库错误的基类。"""


class ConversationAlreadyExistsError(ConversationRepositoryError):
    """创建会话时发现相同编号已经存在。"""


class ConversationDoesNotExistError(ConversationRepositoryError):
    """更新会话时发现目标编号不存在。"""


class ConversationRepository:
    """保存会话快照，并通过深拷贝隔离仓库内部状态。"""

    def __init__(self) -> None:
        self._conversations: dict[UUID, Conversation] = {}
        self._lock = RLock()

    def add(self, conversation: Conversation) -> Conversation:
        """新增会话；编号重复时拒绝覆盖。"""

        with self._lock:
            if conversation.id in self._conversations:
                raise ConversationAlreadyExistsError(
                    f"会话已经存在：{conversation.id}"
                )
            self._conversations[conversation.id] = conversation.model_copy(deep=True)
            return conversation.model_copy(deep=True)

    def update(self, conversation: Conversation) -> Conversation:
        """替换已有会话快照；不存在时抛出仓库异常。"""

        with self._lock:
            if conversation.id not in self._conversations:
                raise ConversationDoesNotExistError(
                    f"会话不存在：{conversation.id}"
                )
            self._conversations[conversation.id] = conversation.model_copy(deep=True)
            return conversation.model_copy(deep=True)

    def get(self, conversation_id: UUID) -> Conversation | None:
        """按编号读取会话；不存在时返回空值。"""

        with self._lock:
            conversation = self._conversations.get(conversation_id)
            return (
                conversation.model_copy(deep=True)
                if conversation is not None
                else None
            )

    def list_by_buyer(self, buyer_id: BuyerId) -> list[Conversation]:
        """按最近更新时间倒序返回指定买家的会话。"""

        with self._lock:
            conversations = [
                conversation.model_copy(deep=True)
                for conversation in self._conversations.values()
                if conversation.buyer_id == buyer_id
            ]

        conversations.sort(
            key=lambda item: (item.updated_at, item.created_at, str(item.id)),
            reverse=True,
        )
        return conversations

    def clear(self) -> None:
        """清空全部内存会话，主要用于测试和重置演示数据。"""

        with self._lock:
            self._conversations.clear()

    def count(self) -> int:
        """返回当前保存的会话数量。"""

        with self._lock:
            return len(self._conversations)
