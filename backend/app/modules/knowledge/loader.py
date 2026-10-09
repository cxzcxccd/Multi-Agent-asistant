"""从 Markdown 文件读取标题和二级章节。"""

from pathlib import Path
from typing import cast
import hashlib
import json

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
    metadata = {}
    manifest_path = path.parent / "sources.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        # 有来源清单的目录只加载清单中的文档，避免混入旧演示政策。
        if path.stem not in manifest:
            return []
        metadata = manifest[path.stem]
    category_name = metadata.get("category", path.stem)
    if category_name not in valid_categories:
        return []
    category = cast(KnowledgeCategory, category_name)
    raw_text = path.read_text(encoding="utf-8")
    version_text = raw_text + json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    document_version = hashlib.sha256(version_text.encode()).hexdigest()[:12]
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
            if metadata:
                # 每个子块都保留来源和适用范围，避免长文切块后丢失平台信息。
                provenance = (
                    f"平台：{metadata['platform']}；适用范围：{metadata['scope']}\n"
                    f"原文：{metadata['url']}\n"
                    f"采集时间：{metadata['collected_at']}；资料形式：人工核验摘要\n"
                )
                part = provenance + part
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
