"""Markdown 知识加载、检索和 LangGraph 工具测试。"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.ai.tools import knowledge
from app.ai.tools.knowledge import get_knowledge_tools, search_knowledge
from app.db.base import Base
from app.modules.knowledge.loader import load_knowledge_directory
from app.modules.knowledge.embeddings import (
    FastEmbedEmbeddingProvider,
    LocalHashEmbeddingProvider,
)
from app.modules.knowledge.indexer import KnowledgeIndexer
from app.modules.knowledge.repository import KnowledgeRepository
from app.modules.knowledge.service import KnowledgeService
from app.modules.knowledge.evaluation import RagEvaluator
from app.modules.knowledge.schemas import AnswerQualityScores, KnowledgeSearchItem
from app.modules.knowledge.vector_store import InMemoryKnowledgeVectorStore


class FakeVector:
    """模拟 FastEmbed 返回的 NumPy 向量。"""

    def __init__(self, values: list[float]) -> None:
        self.values = values

    def tolist(self) -> list[float]:
        return list(self.values)


class FakeFastEmbedModel:
    """记录文档与查询是否使用了各自的编码入口。"""

    def __init__(self) -> None:
        self.passage_inputs: list[str] = []
        self.query_inputs: list[str] = []

    def passage_embed(self, texts: list[str]):
        self.passage_inputs.extend(texts)
        for _text in texts:
            yield FakeVector([1.0, 0.0, 0.0])

    def query_embed(self, texts: list[str]):
        self.query_inputs.extend(texts)
        for _text in texts:
            yield FakeVector([0.0, 1.0, 0.0])


def create_service(database_path: Path) -> KnowledgeService:
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    repository = KnowledgeRepository(session_factory)
    knowledge_path = Path(__file__).resolve().parents[1] / "data" / "knowledge"
    chunks = load_knowledge_directory(knowledge_path)
    provider = LocalHashEmbeddingProvider()
    vector_store = InMemoryKnowledgeVectorStore()
    KnowledgeIndexer(repository, provider, vector_store).rebuild(chunks)
    return KnowledgeService(
        repository,
        provider,
        vector_store,
        minimum_score=0.12,
    )


def test_fastembed_provider_uses_passage_and_query_encoders() -> None:
    model = FakeFastEmbedModel()
    provider = FastEmbedEmbeddingProvider(
        model_name="BAAI/bge-small-zh-v1.5",
        model=model,
        dimensions=3,
    )

    document_vectors = provider.embed_documents(["保修范围", "退款规则"])
    query_vector = provider.embed_query("手机进水能保修吗")

    assert provider.model_name == "BAAI/bge-small-zh-v1.5"
    assert provider.dimensions == 3
    assert model.passage_inputs == ["保修范围", "退款规则"]
    assert model.query_inputs == ["手机进水能保修吗"]
    assert document_vectors == [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
    assert query_vector == [0.0, 1.0, 0.0]


def test_loader_and_search_return_traceable_policy_sources(tmp_path: Path) -> None:
    service = create_service(tmp_path / "knowledge.db")

    result = service.search("耳机拆封后还能退货吗", "return_policy")

    assert result.total > 0
    assert result.items[0].document == "退换货规则"
    assert result.items[0].category == "return_policy"
    assert result.items[0].source.startswith("return_policy.md#")
    assert result.items[0].score > 0
    assert result.retrieval_mode == "hybrid"


def test_search_respects_category_and_returns_empty_for_unknown_topic(tmp_path: Path) -> None:
    service = create_service(tmp_path / "category.db")

    shipping = service.search("什么时候发货", "shipping_policy")
    unrelated = service.search("火星天气和天文观测")

    assert shipping.items
    assert all(item.category == "shipping_policy" for item in shipping.items)
    assert unrelated.items == []


def test_langgraph_knowledge_tool_returns_context_and_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = create_service(tmp_path / "tool.db")
    monkeypatch.setattr(knowledge, "get_knowledge_tool_service", lambda: service)

    result = search_knowledge.invoke(
        {"query": "退款审核通过后多久到账", "category": "payment_policy"}
    )

    assert [tool.name for tool in get_knowledge_tools()] == ["search_knowledge"]
    assert result["success"] is True
    assert result["answer_context"]
    assert result["sources"][0]["document"] == "支付与退款规则"


def test_incremental_index_only_embeds_changed_chunks(tmp_path: Path) -> None:
    database_path = tmp_path / "incremental.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    repository = KnowledgeRepository(session_factory)
    provider = LocalHashEmbeddingProvider()
    vector_store = InMemoryKnowledgeVectorStore()
    indexer = KnowledgeIndexer(repository, provider, vector_store)
    knowledge_path = Path(__file__).resolve().parents[1] / "data" / "knowledge"
    chunks = load_knowledge_directory(knowledge_path)

    first = indexer.rebuild(chunks)
    second = indexer.rebuild(chunks)
    changed_chunks = list(chunks)
    changed_chunks[0] = changed_chunks[0].model_copy(
        update={"content": changed_chunks[0].content + " 补充说明。"}
    )
    third = indexer.rebuild(changed_chunks)

    assert first.embedded == len(chunks)
    assert second.embedded == 0
    assert second.unchanged == len(chunks)
    assert third.embedded == 1
    assert third.updated == 1


def test_fixed_rag_evaluation_meets_local_baseline(tmp_path: Path) -> None:
    service = create_service(tmp_path / "evaluation.db")
    cases_path = Path(__file__).resolve().parents[1] / "data" / "evaluation" / "rag_cases.json"

    report = RagEvaluator(service, cases_path).run()

    assert report.cases == 12
    assert report.recall_at_k >= 0.9
    assert report.mrr >= 0.9
    assert report.rejection_accuracy == 1.0


class FakeAnswerEngine:
    """生成带来源的固定答案，验证回答评测和结果落盘。"""

    def generate(self, question: str, contexts: list[KnowledgeSearchItem]) -> str:
        if not contexts:
            return "根据现有知识库无法确定。"
        return f"这是依据资料生成的回答。[来源: {contexts[0].source}]"

    def judge(
        self,
        question: str,
        reference_answer: str,
        contexts: list[KnowledgeSearchItem],
        answer: str,
    ) -> AnswerQualityScores:
        return AnswerQualityScores(
            correctness=0.9,
            faithfulness=1.0,
            completeness=0.8,
            reason="回答与参考答案一致且有资料支持",
        )


def test_full_rag_evaluation_scores_answers_and_writes_report(tmp_path: Path) -> None:
    service = create_service(tmp_path / "answer-evaluation.db")
    cases_path = Path(__file__).resolve().parents[1] / "data" / "evaluation" / "rag_cases.json"
    evaluator = RagEvaluator(service, cases_path, FakeAnswerEngine())

    report = evaluator.run()
    detail_path, report_path = evaluator.save(report, tmp_path / "results")

    assert report.answer_cases == 12
    assert report.answer_correctness == 0.9
    assert report.answer_faithfulness == 1.0
    assert report.answer_completeness == 0.8
    assert report.citation_precision == 1.0
    assert report.results[0].generated_answer
    assert report.results[0].retrieval_latency_ms >= 0
    assert detail_path.read_text(encoding="utf-8").count("\n") == 12
    assert "RAG 检索与回答评测报告" in report_path.read_text(encoding="utf-8")
