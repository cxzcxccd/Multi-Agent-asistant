"""内存会话仓库的增删改查和数据隔离测试。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.modules.conversations.repository import (
    ConversationAlreadyExistsError,
    ConversationDoesNotExistError,
    ConversationRepository,
)
from app.modules.conversations.schemas import BuyerId, Conversation


def make_conversation(
    conversation_id: UUID | None = None,
    buyer_id: BuyerId = "A",
    updated_offset: int = 0,
) -> Conversation:
    """创建一份有效的空会话快照。"""

    created_at = datetime(2026, 9, 15, 8, tzinfo=UTC)
    return Conversation(
        id=conversation_id or uuid4(),
        buyer_id=buyer_id,
        title="商品咨询",
        created_at=created_at,
        updated_at=created_at + timedelta(minutes=updated_offset),
    )


def test_repository_adds_and_reads_an_isolated_copy() -> None:
    repository = ConversationRepository()
    original = make_conversation()

    returned = repository.add(original)
    returned.title = "被调用方修改"
    loaded = repository.get(original.id)

    assert loaded is not None
    assert loaded.title == "商品咨询"
    loaded.title = "再次修改"
    loaded_again = repository.get(original.id)
    assert loaded_again is not None
    assert loaded_again.title == "商品咨询"


def test_repository_rejects_duplicate_add_and_missing_update() -> None:
    repository = ConversationRepository()
    conversation = make_conversation()
    repository.add(conversation)

    with pytest.raises(ConversationAlreadyExistsError):
        repository.add(conversation)
    with pytest.raises(ConversationDoesNotExistError):
        repository.update(make_conversation())


def test_repository_updates_existing_conversation() -> None:
    repository = ConversationRepository()
    conversation = make_conversation()
    repository.add(conversation)
    updated = conversation.model_copy(
        update={
            "title": "更新后的标题",
            "updated_at": conversation.updated_at + timedelta(minutes=1),
        }
    )

    repository.update(updated)

    loaded = repository.get(conversation.id)
    assert loaded is not None
    assert loaded.title == "更新后的标题"


def test_repository_lists_only_the_buyer_conversations_by_recent_update() -> None:
    repository = ConversationRepository()
    older = make_conversation(buyer_id="A", updated_offset=1)
    newest = make_conversation(buyer_id="A", updated_offset=3)
    repository.add(older)
    repository.add(make_conversation(buyer_id="B", updated_offset=5))
    repository.add(newest)

    items = repository.list_by_buyer("A")

    assert [item.id for item in items] == [newest.id, older.id]


def test_repository_clear_removes_all_conversations() -> None:
    repository = ConversationRepository()
    repository.add(make_conversation())

    assert repository.count() == 1
    repository.clear()
    assert repository.count() == 0
    assert repository.list_by_buyer("A") == []
