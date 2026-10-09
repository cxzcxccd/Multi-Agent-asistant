"""真实来源加载及平台溯源测试，不访问网络。"""

import json
from pathlib import Path

from app.modules.knowledge.loader import load_knowledge_directory


def test_official_documents_keep_platform_scope_and_url() -> None:
    directory = Path(__file__).resolve().parents[1] / "data" / "knowledge"
    manifest = json.loads((directory / "sources.json").read_text(encoding="utf-8"))
    chunks = load_knowledge_directory(directory)
    assert len(manifest) == 6
    assert len(chunks) == 47
    platforms = set()
    for chunk in chunks:
        metadata = manifest[chunk.document_key]
        platforms.add(metadata["platform"])
        assert metadata["url"] in chunk.content
        assert metadata["scope"] in chunk.content
        assert chunk.category == metadata["category"]
        assert chunk.source.startswith(chunk.document_key + ".md#")
    assert platforms == {"京东", "淘宝"}


def test_manifest_excludes_old_demo_and_preserves_provenance_in_long_chunks(tmp_path: Path) -> None:
    metadata = {
        "category": "return_policy",
        "platform": "淘宝",
        "scope": "仅适用于指定平台",
        "url": "https://terms.alicdn.com/example",
        "collected_at": "2026-10-09",
    }
    manifest = {"official": metadata}
    (tmp_path / "sources.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "official.md").write_text("# 真实资料\n## 退货\n" + "长政策正文。" * 200, encoding="utf-8")
    (tmp_path / "return_policy.md").write_text("# 旧政策\n## 旧规则\n不能再加载", encoding="utf-8")
    chunks = load_knowledge_directory(tmp_path)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.document_key == "official"
        assert metadata["url"] in chunk.content
        assert metadata["scope"] in chunk.content
