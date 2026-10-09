"""按排名融合不同检索器的候选结果，不混合不同尺度的原始分数。"""


def reciprocal_rank_fusion(rankings: list[list[str]], rank_constant: int = 60) -> dict[str, float]:
    """排名从 1 开始；未命中某一路的文档不获得该路分数。"""

    if rank_constant < 1:
        raise ValueError("RRF 排名常量必须大于等于 1")
    scores: dict[str, float] = {}
    for document_ids in rankings:
        seen = set()
        rank = 0
        for document_id in document_ids:
            # 同一路重复返回一个文档时，只计首次排名，且不占用后续名次。
            if document_id in seen:
                continue
            seen.add(document_id)
            rank += 1
            contribution = 1 / (rank_constant + rank)
            scores[document_id] = scores.get(document_id, 0.0) + contribution
    return scores
