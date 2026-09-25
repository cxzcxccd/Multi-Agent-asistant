"""从 Markdown 文件读取标题和二级章节。"""

from pathlib import Path
from typing import cast
import hashlib

from app.core.config import settings
from app.modules.knowledge.schemas import KnowledgeCategory, KnowledgeChunk
from app.modules.knowledge.splitter import split_text


def load_knowledge_directory(directory: Path) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    for path in sorted(directory.glob("*.md")):
        chunks.extend(load_knowledge_document(path))
    return chunks


def load_knowledge_document(path: Path) -> list[KnowledgeChunk]:
    valid_categories = {
        "return_policy",
        "warranty_policy",
        "shipping_policy",
        "payment_policy",
        "product_guide",
    }
    if path.stem not in valid_categories:
        return []
    category = cast(KnowledgeCategory, path.stem)
    raw_text = path.read_text(encoding="utf-8")
    document_version = hashlib.sha256(raw_text.encode()).hexdigest()[:12]
    lines = raw_text.splitlines()
    title = path.stem
    section = "正文"
    paragraphs: list[str] = []
    chunks: list[KnowledgeChunk] = []

    def append_section() -> None:
        content = "\n".join(paragraphs).strip()
        if not content:
            return
        parts = split_text(
            content,
            chunk_size=settings.knowledge_chunk_size,
            overlap=settings.knowledge_chunk_overlap,
        )
        for part_index, part in enumerate(parts):
            source = f"{path.name}#{section}"
            if len(parts) > 1:
                source = f"{source}-{part_index + 1}"
            chunks.append(
                KnowledgeChunk(
                    document_key=path.stem,
                    title=title,
                    category=category,
                    section=section,
                    content=part,
                    source=source,
                    position=len(chunks),
                    document_version=document_version,
                )
            )
        paragraphs.clear()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped[2:].strip()
            continue
        if stripped.startswith("## "):
            append_section()
            section = stripped[3:].strip()
            continue
        if stripped:
            paragraphs.append(stripped)
    append_section()
    return chunks
