"""验证记忆隔离、持久化、摘要来源和按需检索。"""

from datetime import UTC, datetime
from uuid import uuid4

from langchain_core.messages import HumanMessage
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.modules.conversations.memory import (
    BuyerMemoryRecord, ConversationSummaryRecord, MemoryService,
)
from app.modules.conversations.schemas import Conversation, ConversationMessage


class MatchingEmbeddings:
    """让测试关注身份和来源，而不依赖下载模型。"""

    def embed_documents(self, texts):
        vectors = []
        for text in texts:
            vectors.append([1.0, 0.0] if "耳机" in text else [0.0, 1.0])
        return vectors

    def embed_query(self, query):
        return [1.0, 0.0]


def make_conversation(buyer="A", count=1):
    now = datetime.now(UTC)
    messages = []
    for index in range(count):
        messages.append(ConversationMessage(
            id=uuid4(), role="user", content=f"耳机需求 {index}", created_at=now,
        ))
    return Conversation(
        id=uuid4(), buyer_id=buyer, title="咨询", mode="ai",
        created_at=now, updated_at=now, messages=messages,
    )


def make_memory(tmp_path, conversations):
    engine = create_engine(f"sqlite:///{tmp_path / 'memory.db'}")
    BuyerMemoryRecord.__table__.create(engine)
    ConversationSummaryRecord.__table__.create(engine)
    def own_conversations(buyer):
        result = []
        for conversation in conversations:
            if conversation.buyer_id == buyer:
                result.append(conversation)
        return result
    return MemoryService(sessionmaker(engine), own_conversations, MatchingEmbeddings)


def test_preferences_persist_replace_delete_and_isolate(tmp_path):
    memory = make_memory(tmp_path, [])
    memory.set_preferences("A", "喜欢入耳式")
    assert memory.preferences("B") == ""
    recreated = MemoryService(memory.session_factory, lambda buyer: [])
    assert recreated.preferences("A") == "喜欢入耳式"
    recreated.set_preferences("A", "喜欢头戴式")
    assert memory.preferences("A") == "喜欢头戴式"
    memory.set_preferences("A", "")
    assert recreated.preferences("A") == ""


def test_summary_refreshes_without_deleting_originals(tmp_path):
    conversation = make_conversation(count=30)
    memory = make_memory(tmp_path, [conversation])
    summary = memory.summary(conversation)
    assert str(conversation.messages[0].id) in summary
    assert "需求 29" not in summary
    conversation.messages[0].content = "更新后的耳机预算"
    assert "更新后的耳机预算" in memory.summary(conversation)
    assert len(conversation.messages) == 30


def test_history_is_on_demand_scoped_and_deduplicated(tmp_path):
    own = make_conversation()
    other = make_conversation("B")
    memory = make_memory(tmp_path, [own, other])
    assert memory.retrieve("A", "推荐耳机", set()) == []
    hits = memory.retrieve("A", "之前的耳机需求", set())
    assert len(hits) == 1
    assert hits[0]["message_id"] == str(own.messages[0].id)
    assert memory.retrieve("A", "之前的耳机需求", {str(own.messages[0].id)}) == []


def test_context_is_bounded_and_latest_question_remains_last(tmp_path):
    conversation = make_conversation(count=40)
    memory = make_memory(tmp_path, [conversation])
    messages = []
    for message in conversation.messages:
        messages.append(HumanMessage(content=message.content, id=str(message.id)))
    messages.append(HumanMessage(content="新问题"))
    context = memory.build_context(conversation, "新问题", messages)
    assert len(context) == 14
    assert context[-1].content == "新问题"
    assert "记忆参考资料" in context[0].content


def test_new_turn_does_not_restore_trimmed_checkpoint_history():
    from langgraph.graph import add_messages
    from app.ai.multi_agent.runtime import MultiAgentRuntime

    previous = [HumanMessage(content="很久以前的消息", id="old")]
    latest = [HumanMessage(content="当前问题", id="new")]
    state = MultiAgentRuntime._initial_state(latest)
    restored = add_messages(previous, state["messages"])
    assert len(restored) == 1
    assert restored[0].id == "new"
