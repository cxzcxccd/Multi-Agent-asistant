"""小规模知识库的 BM25 检索，保留词频与文档长度信息。"""

import math
import re
from collections import Counter


LATIN_WORD_PATTERN = re.compile(r"[a-z0-9]+")
CHINESE_PATTERN = re.compile(r"[\u4e00-\u9fff]+")


def tokenize(text: str) -> list[str]:
    """英文数字按连续词项，中文按相邻双字切分；重复词项用于统计词频。"""

    normalized = text.lower()
    tokens = LATIN_WORD_PATTERN.findall(normalized)
    for sequence in CHINESE_PATTERN.findall(normalized):
        if len(sequence) == 1:
            tokens.append(sequence)
            continue
        for index in range(len(sequence) - 1):
            tokens.append(sequence[index : index + 2])
    return tokens


class BM25Index:
    """根据本次知识库快照计算 BM25，避免文档更新后复用过期统计。"""

    def __init__(self, documents: dict[str, str], k1: float = 1.2, b: float = 0.75) -> None:
        if not math.isfinite(k1) or k1 <= 0:
            raise ValueError("BM25 k1 必须是正有限数")
        if not math.isfinite(b) or not 0 <= b <= 1:
            raise ValueError("BM25 b 必须在 0 到 1 之间")
        self.k1 = k1
        self.b = b
        self.document_frequencies: Counter[str] = Counter()
        self.term_frequencies: dict[str, Counter[str]] = {}
        self.document_lengths: dict[str, int] = {}
        total_length = 0
        for document_id, text in documents.items():
            tokens = tokenize(text)
            frequencies = Counter(tokens)
            self.term_frequencies[document_id] = frequencies
            self.document_lengths[document_id] = len(tokens)
            total_length += len(tokens)
            # 每个词项在一篇文档中只计一次，用于统计文档频率。
            self.document_frequencies.update(frequencies.keys())
        self.document_count = len(documents)
        self.average_length = total_length / max(self.document_count, 1)

    def score(self, query: str) -> dict[str, float]:
        query_terms = set(tokenize(query))
        scores: dict[str, float] = {}
        for document_id, frequencies in self.term_frequencies.items():
            document_length = self.document_lengths[document_id]
            average_length = self.average_length if self.average_length > 0 else 1
            length_ratio = document_length / average_length
            length_normalization = self.k1 * (1 - self.b + self.b * length_ratio)
            document_score = 0.0
            for term in sorted(query_terms):
                term_frequency = frequencies.get(term, 0)
                if term_frequency == 0:
                    continue
                document_frequency = self.document_frequencies[term]
                inverse_frequency = math.log1p(
                    (self.document_count - document_frequency + 0.5)
                    / (document_frequency + 0.5)
                )
                frequency_weight = term_frequency * (self.k1 + 1)
                frequency_weight /= term_frequency + length_normalization
                document_score += inverse_frequency * frequency_weight
            scores[document_id] = document_score
        return scores
