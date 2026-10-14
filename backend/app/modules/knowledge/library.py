"""将当前入库正文组织为可浏览的知识文档，不访问向量数据库。"""

import hashlib
from datetime import UTC, datetime

from app.modules.knowledge.schemas import (
    KnowledgeChunk,
    KnowledgeLibraryDocument,
    KnowledgeLibraryResponse,
)


def _utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def build_library(chunks: list[KnowledgeChunk]) -> KnowledgeLibraryResponse:
    grouped: dict[str, list[KnowledgeChunk]] = {}
    for chunk in sorted(chunks, key=lambda item: (item.document_key, item.position)):
        grouped.setdefault(chunk.document_key, []).append(chunk.model_copy(update={
            "created_at": _utc(chunk.created_at),
            "updated_at": _utc(chunk.updated_at),
        }))
    documents = []
    for key, items in grouped.items():
        timestamps = [item.updated_at for item in items if item.updated_at is not None]
        documents.append(KnowledgeLibraryDocument(
            document_key=key,
            title=items[0].title,
            category=items[0].category,
            document_versions=sorted({item.document_version for item in items}),
            updated_at=max(timestamps) if timestamps else None,
            chunks=items,
        ))
    content = "\n".join(document.model_dump_json() for document in documents)
    return KnowledgeLibraryResponse(
        documents=documents,
        total_documents=len(documents),
        total_chunks=len(chunks),
        revision=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )
