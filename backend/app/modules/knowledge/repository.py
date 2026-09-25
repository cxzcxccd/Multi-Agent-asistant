"""知识分段正文和版本信息的关系数据库访问。"""
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.knowledge.models import KnowledgeChunkRecord
from app.modules.knowledge.schemas import KnowledgeCategory, KnowledgeChunk


class KnowledgeRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def replace_all(self, chunks: list[KnowledgeChunk]) -> None:
        """在同一个事务中替换全部知识分段。"""

        with self.session_factory.begin() as session:
            session.execute(delete(KnowledgeChunkRecord))
            for chunk in chunks:
                values = chunk.model_dump(exclude={"id"})
                current_time = datetime.now(UTC)
                values["created_at"] = chunk.created_at or current_time
                values["updated_at"] = chunk.updated_at or current_time
                session.add(KnowledgeChunkRecord(**values))

    def list_chunks(
        self,
        category: KnowledgeCategory | None = None,
    ) -> list[KnowledgeChunk]:
        with self.session_factory() as session:
            statement = select(KnowledgeChunkRecord)
            if category is not None:
                statement = statement.where(KnowledgeChunkRecord.category == category)
            statement = statement.order_by(
                KnowledgeChunkRecord.document_key,
                KnowledgeChunkRecord.position,
            )
            records = session.scalars(statement).all()
            chunks: list[KnowledgeChunk] = []
            for record in records:
                chunks.append(self._to_schema(record))
            return chunks

    def count(self) -> int:
        with self.session_factory() as session:
            records = session.scalars(select(KnowledgeChunkRecord.id)).all()
            return len(records)

    @staticmethod
    def _to_schema(record: KnowledgeChunkRecord) -> KnowledgeChunk:
        return KnowledgeChunk(
            id=record.id,
            document_key=record.document_key,
            title=record.title,
            category=record.category,
            section=record.section,
            content=record.content,
            source=record.source,
            position=record.position,
            document_version=record.document_version,
            content_hash=record.content_hash,
            embedding_model=record.embedding_model,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
