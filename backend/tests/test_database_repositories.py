"""SQLAlchemy 商品与会话仓库的持久化测试。"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.modules.catalog.models import ProductRecord
from app.modules.catalog.repository import SqlProductRepository
from app.modules.conversations.repository import (
    ConversationAlreadyExistsError,
    SqlConversationRepository,
)
from app.modules.conversations.schemas import (
    Conversation,
    ConversationMessage,
    MessageRole,
)


def make_session_factory(database_path: Path) -> sessionmaker[Session]:
    """为每项测试创建互不影响的 SQLite 数据库。"""

    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def make_conversation(title: str = "耳机咨询") -> Conversation:
    """创建一段包含用户消息的有效会话。"""

    created_at = datetime(2026, 9, 16, 8, 0, tzinfo=UTC)
    message = ConversationMessage(
        id=uuid4(),
        role=MessageRole.USER,
        content="推荐一款耳机",
        created_at=created_at,
    )
    return Conversation(
        id=uuid4(),
        buyer_id="A",
        title=title,
        messages=[message],
        created_at=created_at,
        updated_at=created_at,
    )


def test_sql_product_repository_reads_database_records(tmp_path: Path) -> None:
    session_factory = make_session_factory(tmp_path / "catalog.db")
    with session_factory.begin() as session:
        session.add(
            ProductRecord(
                id="p01",
                name="测试耳机",
                category="耳机",
                series="TEST",
                price=299,
                stock=8,
                specs=["蓝牙 5.3"],
                description="数据库商品",
                color="blue",
                source="KB-TEST",
            )
        )

    repository = SqlProductRepository(session_factory)

    assert [product.id for product in repository.list_products()] == ["p01"]
    product = repository.get_product("p01")
    assert product is not None
    assert product.stock == 8
    assert repository.get_product("p99") is None


def test_sql_conversation_repository_persists_and_updates_messages(
    tmp_path: Path,
) -> None:
    session_factory = make_session_factory(tmp_path / "conversations.db")
    repository = SqlConversationRepository(session_factory)
    conversation = make_conversation()

    repository.add(conversation)
    saved = repository.get(conversation.id)

    assert saved == conversation
    assistant_message = ConversationMessage(
        id=uuid4(),
        role=MessageRole.ASSISTANT,
        content="可以看看 AirBeat Pro。",
        created_at=conversation.created_at + timedelta(seconds=1),
    )
    updated = conversation.model_copy(
        update={
            "messages": [*conversation.messages, assistant_message],
            "updated_at": assistant_message.created_at,
        }
    )
    repository.update(updated)

    reloaded = repository.get(conversation.id)
    assert reloaded == updated
    assert repository.count() == 1


def test_sql_conversation_repository_is_shared_between_instances(
    tmp_path: Path,
) -> None:
    session_factory = make_session_factory(tmp_path / "shared.db")
    first_repository = SqlConversationRepository(session_factory)
    second_repository = SqlConversationRepository(session_factory)
    conversation = make_conversation()

    first_repository.add(conversation)

    assert second_repository.get(conversation.id) == conversation
    assert second_repository.list_by_buyer("A") == [conversation]
    assert second_repository.list_by_buyer("B") == []


def test_sql_conversation_repository_rejects_duplicate_ids(tmp_path: Path) -> None:
    session_factory = make_session_factory(tmp_path / "duplicates.db")
    repository = SqlConversationRepository(session_factory)
    conversation = make_conversation()
    repository.add(conversation)

    with pytest.raises(ConversationAlreadyExistsError, match="会话已经存在"):
        repository.add(conversation)
