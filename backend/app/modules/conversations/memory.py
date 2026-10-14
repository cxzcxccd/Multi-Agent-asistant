"""买家偏好、会话摘录摘要与按需历史检索；原始消息始终保留。"""

import logging
import hashlib
import math
import re
from collections.abc import Callable, Sequence

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, sessionmaker

from app.core.tracing import traced, span, emit

from app.db.base import Base
from app.ai.context import derive_context, context_message, ContextEntity
from app.ai.scenes import project_scenes, SceneSnapshot, scene_sections
from app.modules.conversations.schemas import Conversation, MessageRole
from app.modules.knowledge.embeddings import EmbeddingProvider, create_embedding_provider

logger = logging.getLogger(__name__)


class BuyerMemoryRecord(Base):
    """每个买家一份显式偏好，允许覆盖和删除。"""

    __tablename__ = "buyer_memories"
    buyer_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    preferences: Mapped[str] = mapped_column(Text, nullable=False)


class ConversationSummaryRecord(Base):
    """缓存旧消息摘要，签名变化时重建，不替代原始消息。"""

    __tablename__ = "conversation_summaries"
    conversation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_signature: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    scene_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    scenes_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class MemoryService:
    """将有界记忆组装进上下文，不授予记忆任何业务操作权限。"""

    def __init__(
        self,
        session_factory: sessionmaker,
        list_conversations: Callable[[str], list[Conversation]],
        embedding_factory: Callable[[], EmbeddingProvider] = create_embedding_provider,
        recent_messages: int = 12,
        history_candidates: int = 200,
    ) -> None:
        self.session_factory = session_factory
        self.list_conversations = list_conversations
        self.embedding_factory = embedding_factory
        self.recent_messages = recent_messages
        self.history_candidates = history_candidates
        self._embedding_provider: EmbeddingProvider | None = None

    def preferences(self, buyer_id: str) -> str:
        with self.session_factory() as session:
            record = session.get(BuyerMemoryRecord, buyer_id)
            if record is None:
                return ""
            return record.preferences

    def set_preferences(self, buyer_id: str, content: str) -> None:
        with self.session_factory.begin() as session:
            record = session.get(BuyerMemoryRecord, buyer_id)
            if not content.strip():
                if record is not None:
                    session.delete(record)
                return
            if record is None:
                record = BuyerMemoryRecord(buyer_id=buyer_id, preferences=content.strip())
                session.add(record)
            else:
                record.preferences = content.strip()

    def summary(self, conversation: Conversation) -> str:
        old_messages = conversation.messages[:-self.recent_messages]
        if not old_messages:
            return ""
        # 每条旧消息保留来源编号；有界摘录不声称完整覆盖所有历史。
        signature_parts = []
        for message in old_messages:
            signature_parts.append(str(message.id) + ":" + message.content)
        signature = hashlib.sha256("\n".join(signature_parts).encode()).hexdigest()
        with self.session_factory.begin() as session:
            record = session.get(ConversationSummaryRecord, str(conversation.id))
            if record is not None and record.source_signature == signature:
                return record.content
            excerpts = []
            for message in old_messages[-20:]:
                if message.role is MessageRole.SYSTEM:
                    continue
                excerpts.append(f"[{message.id}] {message.role.value}：{message.content[:120]}")
            content = "旧对话摘录摘要（可能不完整，不是实时业务事实）：\n" + "\n".join(excerpts)
            if record is None:
                record = ConversationSummaryRecord(
                    conversation_id=str(conversation.id),
                    source_signature=signature,
                    content=content,
                )
                session.add(record)
            else:
                record.source_signature = signature
                record.content = content
            return content

    @traced("retrieval.memory")
    def retrieve(self, buyer_id: str, query: str, excluded_ids: set[str]) -> list[dict]:
        """只检索当前身份的用户原话，并返回原消息来源。"""
        if re.search(r"之前|上次|以前|历史|还记得|曾经|刚才", query) is None:
            return []
        candidates = []
        conversations = self.list_conversations(buyer_id)
        conversations.sort(key=lambda item: item.updated_at, reverse=True)
        for conversation in conversations:
            for message in reversed(conversation.messages):
                if message.role is not MessageRole.USER or str(message.id) in excluded_ids:
                    continue
                candidates.append((conversation, message))
                if len(candidates) >= self.history_candidates:
                    break
            if len(candidates) >= self.history_candidates:
                break
        if not candidates:
            return []
        texts = []
        for _, message in candidates:
            texts.append(message.content)
        if self._embedding_provider is None:
            self._embedding_provider = self.embedding_factory()
        provider = self._embedding_provider
        vectors = provider.embed_documents(texts)
        query_vector = provider.embed_query(query)
        ranked = []
        for (conversation, message), vector in zip(candidates, vectors, strict=True):
            dot_product = 0.0
            for left, right in zip(query_vector, vector, strict=True):
                dot_product += left * right
            query_length_squared = 0.0
            for value in query_vector:
                query_length_squared += value * value
            document_length_squared = 0.0
            for value in vector:
                document_length_squared += value * value
            query_norm = math.sqrt(query_length_squared)
            document_norm = math.sqrt(document_length_squared)
            if query_norm == 0 or document_norm == 0:
                continue
            score = dot_product / (query_norm * document_norm)
            if score >= 0.55:
                ranked.append({
                    "conversation_id": str(conversation.id),
                    "message_id": str(message.id),
                    "content": message.content,
                    "score": score,
                })
        ranked.sort(key=lambda item: item["score"], reverse=True)
        return ranked[:3]

    def build_context(
        self, conversation: Conversation, query: str, messages: Sequence[BaseMessage]
    ) -> list[BaseMessage]:
        recent = list(messages[-(self.recent_messages + 1):])
        sections = []
        preferences = self.preferences(conversation.buyer_id)
        if preferences:
            sections.append("买家显式偏好：" + preferences)
        self.scenes(conversation)
        snapshot = project_scenes(messages)
        scene_required, archived = scene_sections(snapshot)
        excluded_ids = set()
        for message in recent:
            excluded_ids.add(str(message.id))
        try:
            hits = self.retrieve(conversation.buyer_id, query, excluded_ids)
        except Exception:
            # 记忆检索失败不阻断正常聊天，但必须留下可排查日志。
            logger.exception("历史记忆检索失败，会话=%s", conversation.id)
            hits = []
        for hit in hits:
            sections.append(f"历史原话 [{hit['conversation_id']}/{hit['message_id']}]：{hit['content'][:500]}")
        # Project from complete current-session messages before trimming history.
        # Persisted conversation messages are the source of truth; no second state
        # table can drift when a turn fails or an old message is corrected.
        state = derive_context(messages)
        active = next((scene for scene in snapshot.scenes if scene.id == snapshot.active_scene_id), None)
        if state.activation is None and active and state.product and (
            active.object_key == "product:" + str(state.product.value)
        ):
            activation = next((value for value in active.conditions if value in {"已激活", "未激活"}), None)
            if activation:
                source_id = active.condition_sources.get(activation)
                source = next((message for message in messages if str(message.id) == source_id), None)
                state.activation = ContextEntity(value=activation, candidates=[activation],
                    source_message_id=source_id,
                    source_created_at=source.additional_kwargs.get("source_created_at") if source else None,
                    evidence=str(source.content)[:200] if source else "",
                    confirmation="user_explicit")
        state.conversation_id = str(conversation.id)
        state.buyer_id = conversation.buyer_id
        state.identity_source = "backend"
        memory = context_message(state, scene_required)
        required = str(memory.content)
        optional = [*sections, *archived]
        memory = memory.model_copy(update={
            "content": required + ("\n" + "\n".join(optional) if optional else ""),
            "additional_kwargs": {**memory.additional_kwargs,
                "context_parts": {"required": required, "optional": optional}},
        })
        return [memory, *recent]

    def scenes(self, conversation: Conversation) -> SceneSnapshot:
        """Persist scene provenance from saved messages, never a failed provisional turn."""
        messages = [(HumanMessage if message.role is MessageRole.USER else AIMessage)(
                        content=message.content, id=str(message.id))
                    for message in conversation.messages if message.role is not MessageRole.SYSTEM]
        signature = hashlib.sha256(("scene-projection-v1\n" + "\n".join(str(message.id) + ":" + message.type + ":" + str(message.content)
                                             for message in messages)).encode()).hexdigest()
        with self.session_factory.begin() as session:
            record = session.get(ConversationSummaryRecord, str(conversation.id))
            if record and record.scene_signature == signature and record.scenes_json:
                return SceneSnapshot.model_validate_json(record.scenes_json)
            snapshot = project_scenes(messages)
            if record is None:
                record = ConversationSummaryRecord(conversation_id=str(conversation.id), source_signature="", content="")
                session.add(record)
            record.scene_signature = signature
            record.scenes_json = snapshot.model_dump_json()
            return snapshot
