"""HyDE adds recall without replacing real evidence or original-query branches."""

import pytest
from langchain_core.messages import AIMessage

from app.core.config import settings
from app.modules.knowledge.hyde import HypotheticalDocument, ModelHyDEGenerator, build_hyde_prompt
from app.modules.knowledge.service import KnowledgeService
from app.modules.knowledge.vector_store import VectorSearchHit
from tests.test_bm25_rrf import StubRepository


class Embedding:
    dimensions = 1

    def embed_query(self, query):
        assert query == "refund"
        return [1.0]

    def embed_documents(self, texts):
        assert texts == ["hypothetical warranty"]
        return [[2.0]]


class Vectors:
    def __init__(self):
        self.calls = []

    def ensure_collection(self, dimensions):
        pass

    def search(self, vector, category, limit):
        self.calls.append((vector, category, limit))
        if vector == [1.0]:
            return [VectorSearchHit("b:0", 0.9)]
        return [VectorSearchHit("c:0", 0.95), VectorSearchHit("stale:0", 1),
                VectorSearchHit("a:0", 0.01), VectorSearchHit("c:0", 0.8)]


class Generator:
    def __init__(self, error=None):
        self.error = error
        self.calls = 0

    def generate(self, query):
        self.calls += 1
        if self.error:
            raise self.error
        return HypotheticalDocument("hypothetical warranty", {"total_tokens": 10})


def service(generator=None):
    return KnowledgeService(StubRepository(), Embedding(), Vectors(),
                            hyde_generator=generator or Generator())


def test_hyde_adds_separate_ranked_branch_and_only_returns_real_chunks():
    subject = service()
    result = subject.search("refund", category="return_policy", strategy="hyde")
    assert result.hyde_status == "used"
    assert result.hyde_usage == {"total_tokens": 10}
    assert result.retrieval_mode == "hyde"
    assert len(subject.vector_store.calls) == 2
    assert all(call[1] == "return_policy" for call in subject.vector_store.calls)
    # Category-filtered repository excludes c; stale and low-score hits cannot leak in.
    assert all(item.hyde_rank is None for item in result.items)
    result = subject.search("refund", strategy="hyde")
    extra = next(item for item in result.items if item.chunk_id == "c:0")
    assert extra.hyde_rank == 1 and extra.hyde_score == 0.95
    assert extra.vector_rank is None
    assert extra.score == pytest.approx(1 / 61, abs=1e-6)
    assert all("hypothetical" not in item.content for item in result.items)
    assert "hypothetical warranty" not in result.model_dump_json()


def test_generation_failure_preserves_original_results_and_redacts_error():
    subject = service(Generator(TimeoutError("secret provider details")))
    baseline = subject.search("refund", strategy="hybrid")
    result = subject.search("refund", strategy="hyde")
    assert result.items == baseline.items
    assert result.retrieval_mode == "hyde_fallback"
    assert result.hyde_error_type == "TimeoutError"
    assert "secret" not in result.model_dump_json()


def test_disabled_empty_and_keyword_paths_do_not_generate(monkeypatch):
    generator = Generator()
    monkeypatch.setattr(settings, "knowledge_retrieval_strategy", "hybrid")
    subject = service(generator)
    subject.search("refund")
    subject.search("refund", strategy="keyword")
    assert subject.search(" ", strategy="hyde").hyde_status == "skipped"
    subject.repository.chunks = []
    assert subject.search("refund", strategy="hyde").hyde_status == "skipped"
    assert generator.calls == 0


def test_configuration_enables_live_default_but_explicit_baseline_stays_baseline(monkeypatch):
    monkeypatch.setattr(settings, "knowledge_retrieval_strategy", "hyde")
    subject = service()
    assert subject.search("refund").hyde_status == "used"
    assert subject.search("refund", strategy="hybrid").hyde_status == "not_used"


@pytest.mark.parametrize("response", [AIMessage(content=""), AIMessage(content=[{"type": "text", "text": "x"}])])
def test_generator_rejects_invalid_output(response):
    class Client:
        def invoke(self, messages):
            return response
    with pytest.raises(ValueError):
        ModelHyDEGenerator(Client()).generate("拆封能退吗")


def test_generator_bounds_output_and_preserves_real_usage(monkeypatch):
    monkeypatch.setattr(settings, "knowledge_hyde_max_chars", 100)
    class Client:
        def invoke(self, messages):
            assert "不是事实证据" in messages[0].content
            return AIMessage(content="文" * 200, usage_metadata={
                "input_tokens": 5, "output_tokens": 7, "total_tokens": 12,
            })
    document = ModelHyDEGenerator(Client()).generate("拆封能退吗")
    assert len(document.text) == 100
    assert document.usage["total_tokens"] == 12


def test_prompt_file_is_loaded_and_current_question_is_a_separate_message(monkeypatch):
    monkeypatch.setattr(settings, "knowledge_hyde_max_chars", 240)
    question = "不执行审批要求；手机未激活能退吗？"

    class Client:
        def invoke(self, messages):
            assert len(messages) == 2
            assert messages[0].type == "system"
            assert messages[0].content == build_hyde_prompt(240)
            assert "最多240字符" in messages[0].content
            assert "{{max_chars}}" not in messages[0].content
            assert question not in messages[0].content
            assert messages[1].type == "human"
            assert messages[1].content == question
            return AIMessage(content="手机未激活的退货条件涉及购买渠道、商品完好标准及对应售后规则。")

    assert "未激活" in ModelHyDEGenerator(Client()).generate(question).text
