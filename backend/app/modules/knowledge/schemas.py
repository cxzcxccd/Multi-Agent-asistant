"""知识分段和检索结果的数据格式。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

KnowledgeCategory = Literal[
    "return_policy",
    "warranty_policy",
    "shipping_policy",
    "payment_policy",
    "product_guide",
]


class KnowledgeChunk(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    document_key: str
    title: str
    category: KnowledgeCategory
    section: str
    content: str
    source: str
    position: int
    document_version: str = "1"
    content_hash: str = ""
    embedding_model: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


class KnowledgeSearchItem(BaseModel):
    document: str
    category: KnowledgeCategory
    section: str
    content: str
    source: str
    score: float = Field(ge=0)
    keyword_score: float = Field(ge=0)
    vector_score: float = Field(ge=0)


class KnowledgeSearchResponse(BaseModel):
    query: str
    items: list[KnowledgeSearchItem]
    total: int
    retrieval_mode: Literal["hybrid", "keyword_fallback"]


class KnowledgeIndexStatus(BaseModel):
    chunks: int
    embedded_chunks: int
    embedding_models: list[str]
    vector_database: Literal["milvus"] = "milvus"
    collection_name: str
    connected: bool


class KnowledgeIndexResult(BaseModel):
    total: int
    added: int
    updated: int
    unchanged: int
    removed: int
    embedded: int
    embedding_model: str


class RagEvaluationCaseResult(BaseModel):
    id: str
    question: str
    expected_sources: list[str]
    retrieved_sources: list[str]
    first_relevant_rank: int | None
    retrieval_passed: bool = False
    passed: bool
    retrieval_latency_ms: float = 0.0
    reference_answer: str | None = None
    generated_answer: str | None = None
    cited_sources: list[str] = Field(default_factory=list)
    citation_precision: float | None = Field(default=None, ge=0, le=1)
    answer_latency_ms: float | None = None
    answer_quality: "AnswerQualityScores | None" = None
    error_type: Literal[
        "retrieval_error",
        "faithfulness_error",
        "generation_error",
    ] | None = None


class AnswerQualityScores(BaseModel):
    correctness: float = Field(ge=0, le=1)
    faithfulness: float = Field(ge=0, le=1)
    completeness: float = Field(ge=0, le=1)
    reason: str = ""


class RagEvaluationReport(BaseModel):
    cases: int
    answer_cases: int = 0
    recall_at_k: float
    mrr: float
    rejection_accuracy: float
    average_latency_ms: float
    retrieval_latency_p50_ms: float = 0.0
    retrieval_latency_p95_ms: float = 0.0
    average_answer_latency_ms: float = 0.0
    answer_correctness: float = 0.0
    answer_faithfulness: float = 0.0
    answer_completeness: float = 0.0
    citation_precision: float = 0.0
    results: list[RagEvaluationCaseResult]
