"""保留 Markdown 章节语义的长文本切分。"""


def split_text(text: str, chunk_size: int = 500, overlap: int = 60) -> list[str]:
    normalized = text.strip()
    if not normalized:
        return []
    if len(normalized) <= chunk_size:
        return [normalized]

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + chunk_size, len(normalized))
        if end < len(normalized):
            paragraph_break = normalized.rfind("\n", start, end)
            sentence_break = normalized.rfind("。", start, end)
            best_break = max(paragraph_break, sentence_break)
            if best_break > start + chunk_size // 2:
                end = best_break + 1
        chunks.append(normalized[start:end].strip())
        if end >= len(normalized):
            break
        start = max(end - overlap, start + 1)
    return chunks
