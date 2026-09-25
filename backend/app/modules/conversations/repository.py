"""内存和 SQLAlchemy 会话数据访问层。"""

from collections.abc import Callable
from datetime import UTC, datetime
from threading import RLock
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.modules.conversations.models import ConversationRecord, MessageRecord
from app.modules.conversations.schemas import BuyerId, Conversation
from app.modules.conversations.schemas import ConversationMessage


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

    def list_all(self) -> list[Conversation]:
        """按最近更新时间倒序返回全部会话。"""

        with self._lock:
            conversations: list[Conversation] = []
            for conversation in self._conversations.values():
                conversations.append(conversation.model_copy(deep=True))
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


class SqlConversationRepository:
    """使用 SQLAlchemy 持久化完整会话和消息。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def add(self, conversation: Conversation) -> Conversation:
        """在一个事务中新增会话和全部消息。"""

        try:
            with self._session_factory.begin() as session:
                session.add(self._to_record(conversation))
        except IntegrityError as exc:
            raise ConversationAlreadyExistsError(
                f"会话已经存在：{conversation.id}"
            ) from exc
        return conversation.model_copy(deep=True)

    def update(self, conversation: Conversation) -> Conversation:
        """在一个事务中更新会话，并替换其消息快照。"""

        with self._session_factory.begin() as session:
            record = session.get(ConversationRecord, str(conversation.id))
            if record is None:
                raise ConversationDoesNotExistError(
                    f"会话不存在：{conversation.id}"
                )

            record.buyer_id = conversation.buyer_id
            record.title = conversation.title
            record.mode = conversation.mode.value
            record.created_at = conversation.created_at
            record.updated_at = conversation.updated_at

            statement = delete(MessageRecord).where(
                MessageRecord.conversation_id == str(conversation.id)
            )
            session.execute(statement)
            for position, message in enumerate(conversation.messages):
                session.add(self._message_to_record(conversation.id, position, message))

        return conversation.model_copy(deep=True)

    def get(self, conversation_id: UUID) -> Conversation | None:
        """按编号读取包含消息的完整会话。"""

        with self._session_factory() as session:
            statement = (
                select(ConversationRecord)
                .options(selectinload(ConversationRecord.messages))
                .where(ConversationRecord.id == str(conversation_id))
            )
            record = session.scalar(statement)
            if record is None:
                return None
            return self._to_schema(record)

    def list_by_buyer(self, buyer_id: BuyerId) -> list[Conversation]:
        """按最近更新时间倒序返回指定买家的完整会话。"""

        with self._session_factory() as session:
            statement = (
                select(ConversationRecord)
                .options(selectinload(ConversationRecord.messages))
                .where(ConversationRecord.buyer_id == buyer_id)
                .order_by(
                    ConversationRecord.updated_at.desc(),
                    ConversationRecord.created_at.desc(),
                    ConversationRecord.id.desc(),
                )
            )
            records = session.scalars(statement).all()
            conversations: list[Conversation] = []
            for record in records:
                conversations.append(self._to_schema(record))
            return conversations

    def list_all(self) -> list[Conversation]:
        """按最近更新时间倒序返回全部完整会话。"""

        with self._session_factory() as session:
            statement = (
                select(ConversationRecord)
                .options(selectinload(ConversationRecord.messages))
                .order_by(
                    ConversationRecord.updated_at.desc(),
                    ConversationRecord.created_at.desc(),
                    ConversationRecord.id.desc(),
                )
            )
            records = session.scalars(statement).all()
            conversations: list[Conversation] = []
            for record in records:
                conversations.append(self._to_schema(record))
            return conversations

    def clear(self) -> None:
        """清空全部会话；消息通过外键级联删除。"""

        with self._session_factory.begin() as session:
            session.execute(delete(ConversationRecord))

    def count(self) -> int:
        """返回当前保存的会话数量。"""

        with self._session_factory() as session:
            count = session.scalar(select(func.count()).select_from(ConversationRecord))
            return int(count or 0)

    @classmethod
    def _to_record(cls, conversation: Conversation) -> ConversationRecord:
        """把会话格式转换为数据库记录。"""

        record = ConversationRecord(
            id=str(conversation.id),
            buyer_id=conversation.buyer_id,
            title=conversation.title,
            mode=conversation.mode.value,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )
        for position, message in enumerate(conversation.messages):
            record.messages.append(
                cls._message_to_record(conversation.id, position, message)
            )
        return record

    @staticmethod
    def _message_to_record(
        conversation_id: UUID,
        position: int,
        message: ConversationMessage,
    ) -> MessageRecord:
        """把一条消息转换为数据库记录。"""

        return MessageRecord(
            id=str(message.id),
            conversation_id=str(conversation_id),
            position=position,
            role=message.role.value,
            content=message.content,
            created_at=message.created_at,
        )

    @classmethod
    def _to_schema(cls, record: ConversationRecord) -> Conversation:
        """把数据库记录转换为会话响应格式。"""

        messages: list[ConversationMessage] = []
        for message in record.messages:
            messages.append(
                ConversationMessage(
                    id=UUID(message.id),
                    role=message.role,
                    content=message.content,
                    created_at=cls._with_timezone(message.created_at),
                )
            )

        return Conversation(
            id=UUID(record.id),
            buyer_id=record.buyer_id,
            title=record.title,
            mode=record.mode,
            messages=messages,
            created_at=cls._with_timezone(record.created_at),
            updated_at=cls._with_timezone(record.updated_at),
        )

    @staticmethod
    def _with_timezone(value: datetime) -> datetime:
        """SQLite 丢失时区标记时，将已约定的 UTC 补回。"""

        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
