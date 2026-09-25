"""知识文档增量索引。"""

import hashlib
from datetime import UTC, datetime

from app.modules.knowledge.embeddings import EmbeddingProvider
from app.modules.knowledge.repository import KnowledgeRepository
from app.modules.knowledge.schemas import KnowledgeChunk, KnowledgeIndexResult
from app.modules.knowledge.vector_store import (
    KnowledgeVectorStore,
    VectorRecord,
    build_vector_id,
)


class KnowledgeIndexer:
    def __init__(
        self,
        repository: KnowledgeRepository,
        provider: EmbeddingProvider,
        vector_store: KnowledgeVectorStore,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.vector_store = vector_store

    def rebuild(self, chunks: list[KnowledgeChunk]) -> KnowledgeIndexResult:
        existing_chunks = self.repository.list_chunks()
        existing_by_key = {
            (chunk.document_key, chunk.position): chunk for chunk in existing_chunks
        }
        self.vector_store.ensure_collection(self.provider.dimensions)
        existing_vectors = self.vector_store.list_metadata()
        incoming_vector_ids = {
            build_vector_id(chunk.document_key, chunk.position) for chunk in chunks
        }
        prepared: list[KnowledgeChunk] = []
        pending_chunks: list[KnowledgeChunk] = []
        pending_texts: list[str] = []
        added = 0
        updated = 0
        unchanged = 0
        now = datetime.now(UTC)

        for chunk in chunks:
            content_hash = self._content_hash(chunk)
            key = (chunk.document_key, chunk.position)
            vector_id = build_vector_id(chunk.document_key, chunk.position)
            existing = existing_by_key.get(key)
            existing_vector = existing_vectors.get(vector_id)
            same_content = existing is not None and existing.content_hash == content_hash
            same_model = existing is not None and existing.embedding_model == self.provider.model_name
            vector_is_current = (
                existing_vector is not None
                and existing_vector.content_hash == content_hash
                and existing_vector.embedding_model == self.provider.model_name
            )
            if same_content and same_model and vector_is_current:
                indexed = chunk.model_copy(
                    update={
                        "content_hash": content_hash,
                        "embedding_model": existing.embedding_model,
                        "created_at": existing.created_at,
                        "updated_at": existing.updated_at,
                    }
                )
                unchanged += 1
            else:
                indexed = chunk.model_copy(
                    update={
                        "content_hash": content_hash,
                        "embedding_model": self.provider.model_name,
                        "created_at": existing.created_at if existing else now,
                        "updated_at": now,
                    }
                )
                pending_chunks.append(indexed)
                pending_texts.append(self._embedding_text(chunk))
                if existing is None:
                    added += 1
                else:
                    updated += 1
            prepared.append(indexed)

        embeddings = self.provider.embed_documents(pending_texts) if pending_texts else []
        vector_records: list[VectorRecord] = []
        for chunk, embedding in zip(pending_chunks, embeddings, strict=True):
            vector_records.append(
                VectorRecord(
                    id=build_vector_id(chunk.document_key, chunk.position),
                    document_key=chunk.document_key,
                    position=chunk.position,
                    category=chunk.category,
                    content_hash=chunk.content_hash,
                    embedding_model=chunk.embedding_model,
                    vector=embedding,
                )
            )

        removed_vector_ids = sorted(set(existing_vectors) - incoming_vector_ids)
        self.vector_store.upsert(vector_records)
        self.vector_store.delete(removed_vector_ids)
        self.repository.replace_all(prepared)
        return KnowledgeIndexResult(
            total=len(prepared),
            added=added,
            updated=updated,
            unchanged=unchanged,
            removed=len(removed_vector_ids),
            embedded=len(pending_texts),
            embedding_model=self.provider.model_name,
        )

    @staticmethod
    def _content_hash(chunk: KnowledgeChunk) -> str:
        value = f"{chunk.title}\n{chunk.section}\n{chunk.content}"
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def _embedding_text(chunk: KnowledgeChunk) -> str:
        return f"{chunk.title}\n{chunk.section}\n{chunk.content}"
