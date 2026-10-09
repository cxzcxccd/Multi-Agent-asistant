"""采集已核验的官方页面，将有来源的摘要导入知识库。"""

import hashlib
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


BACKEND_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = BACKEND_ROOT / "data" / "knowledge_sources"
KNOWLEDGE_DIRECTORY = BACKEND_ROOT / "data" / "knowledge"
ALLOWED_HOSTS = {"help.jd.com", "terms.alicdn.com"}


class VisibleTextParser(HTMLParser):
    """过滤脚本和样式，只提取用于核验的可见文本。"""

    def __init__(self) -> None:
        super().__init__()
        self.hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"script", "style"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def fetch_page(url: str) -> tuple[str, str]:
    if urlparse(url).scheme != "https" or urlparse(url).hostname not in ALLOWED_HOSTS:
        raise ValueError("只允许清单中的官方 HTTPS 域名")
    request = Request(url, headers={"User-Agent": "CommerceKnowledgeImporter/1.0"})
    with urlopen(request, timeout=25) as response:
        if urlparse(response.url).hostname not in ALLOWED_HOSTS:
            raise ValueError("页面跳转到了非官方域名，停止导入")
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("页面过大，停止导入")
        encoding = response.headers.get_content_charset()
    if not encoding:
        match = re.search(br'charset=["\s]*([\w-]+)', raw[:4096], re.IGNORECASE)
        encoding = match.group(1).decode("ascii") if match else "utf-8"
    parser = VisibleTextParser()
    parser.feed(raw.decode(encoding))
    text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def import_documents() -> None:
    configuration = json.loads((SOURCE_DIRECTORY / "sources.json").read_text(encoding="utf-8"))
    collected_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    prepared: list[tuple[str, str, dict]] = []
    manifest: dict[str, dict] = {}
    # 先采集、核验全部页面。任一页面失败时，不覆盖现有知识文档。
    for source in configuration["sources"]:
        document_key = source["document_key"]
        print(f"正在采集：{source['title']}", flush=True)
        text, text_hash = fetch_page(source["url"])
        evidence: list[str] = []
        for section in source["sections"]:
            required_text = section["required_text"]
            if required_text not in text:
                raise ValueError(f"{document_key} 缺少核验文本，可能遇到登录或页面变化：{required_text}")
            evidence.append(required_text)
        snapshot_path = SOURCE_DIRECTORY / f"{document_key}.json"
        if snapshot_path.exists():
            previous = json.loads(snapshot_path.read_text(encoding="utf-8"))
            if previous["text_sha256"] != text_hash:
                raise ValueError(f"{document_key} 页面已变化，请人工核验摘要后更新来源快照")
        metadata = {
            "platform": source["platform"],
            "category": source["category"],
            "url": source["url"],
            "scope": source["scope"],
            "collected_at": collected_at,
            "text_sha256": text_hash,
            "representation": "人工核验摘要，非全文镜像",
        }
        lines = [f"# {source['title']}", ""]
        for section in source["sections"]:
            lines.extend([f"## {section['title']}", "", section["summary"], ""])
        snapshot = dict(metadata)
        snapshot["verified_text_fragments"] = evidence
        manifest[document_key] = metadata
        prepared.append((document_key, "\n".join(lines), snapshot))
    KNOWLEDGE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for document_key, markdown, snapshot in prepared:
        (KNOWLEDGE_DIRECTORY / f"{document_key}.md").write_text(markdown, encoding="utf-8")
        snapshot_path = SOURCE_DIRECTORY / f"{document_key}.json"
        snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest_path = KNOWLEDGE_DIRECTORY / "sources.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已导入 {len(prepared)} 份官方资料摘要；请重建知识索引。")


if __name__ == "__main__":
    import_documents()
