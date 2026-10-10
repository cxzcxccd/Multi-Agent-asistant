"""验证用量归属、缺失费用和工具参数契约，避免实验指标虚高。"""

from langchain_core.messages import AIMessage

from app.modules.knowledge.evaluation import ModelRagAnswerEngine, RagEvaluator
from app.modules.knowledge.schemas import RagEvaluationCaseResult
from scripts.evaluate_rag_runtime import validate_parameters, run_case
from scripts.prepare_jddc_runtime_test import validate_runtime_batch
import pytest


def make_result(**values):
    return RagEvaluationCaseResult(
        id="case", question="问题", expected_sources=[], retrieved_sources=[],
        first_relevant_rank=None, passed=False, **values,
    )


def test_missing_usage_is_not_zero_cost():
    assert RagEvaluator._sum_usage([None]) is None
    assert RagEvaluator._sum_usage([]) is None
    results = [make_result(generation_cost=0.01), make_result()]
    assert RagEvaluator._average_cost(results) is None


def test_task_rate_and_p95_have_separate_denominators():
    results = [
        make_result(task_passed=True, end_to_end_latency_ms=100),
        make_result(task_passed=False, end_to_end_latency_ms=900),
        make_result(),
    ]
    assert RagEvaluator._task_completion_rate(results) == 0.5
    assert RagEvaluator._end_to_end_p95(results) == 900
    assert RagEvaluator._task_completion_rate([make_result()]) is None


def test_usage_sums_only_supplied_calls():
    first = {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13}
    second = {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25}
    assert RagEvaluator._sum_usage([first, second]) == {
        "input_tokens": 30, "output_tokens": 8, "total_tokens": 38,
    }
    assert RagEvaluator._sum_usage([first, None]) is None


def test_parameter_contract_rejects_wrong_fact_category_and_extra_fields():
    case = {"category": "payment_policy", "parameter_terms": ["E卡"]}
    assert validate_parameters({"query": "京东E卡退款"}, case)
    assert not validate_parameters({"query": "手机保修"}, case)
    assert not validate_parameters({"query": "E卡退款", "category": "shipping_policy"}, case)
    assert not validate_parameters({"query": "E卡退款", "buyer_id": "other"}, case)


def test_generation_and_judge_usage_are_recorded_separately():
    class Client:
        def __init__(self):
            self.calls = 0

        def invoke(self, messages):
            self.calls += 1
            content = "无法确定"
            if self.calls == 2:
                content = '{"correctness":1,"faithfulness":1,"completeness":1}'
            return AIMessage(
                content=content,
                usage_metadata={"input_tokens": self.calls * 10, "output_tokens": 2, "total_tokens": self.calls * 10 + 2},
            )

    engine = ModelRagAnswerEngine(Client())
    answer = engine.generate("问题", [])
    engine.judge("问题", "无法确定", [], answer)
    assert engine.usage_records[0]["input_tokens"] == 10
    assert engine.usage_records[1]["input_tokens"] == 20


def test_semantic_parameters_allow_synonyms_and_multiple_categories():
    case = {
        "category": "return_policy", "parameter_check": "semantic",
        "allowed_categories": ["payment_policy", "return_policy"],
    }
    assert validate_parameters({"query": "款项退回原支付渠道"}, case)
    assert validate_parameters({"query": "退款渠道", "category": "payment_policy"}, case)
    assert not validate_parameters({"query": "退款渠道", "category": "shipping_policy"}, case)


def test_current_annotations_cannot_change_original_test_queries():
    original = [{"id": "one", "query": "退款多久", "category": "payment_policy"}]
    result = {
        "id": "one", "query": "退款多久", "category": "payment_policy",
        "should_answer": False, "expected_sources": [], "reference_answer": "无法确定",
        "expected_tool": "search_knowledge", "allowed_categories": ["payment_policy"],
    }
    validate_runtime_batch(original, [result], set())
    result["query"] = "另一个问题"
    with pytest.raises(ValueError, match="不得改写"):
        validate_runtime_batch(original, [result], set())


def test_current_annotations_cannot_reference_old_missing_sources():
    original = [{"id": "one", "query": "退款多久", "category": "payment_policy"}]
    result = {
        "id": "one", "query": "退款多久", "category": "payment_policy",
        "should_answer": True, "expected_sources": ["old.md#旧政策"], "reference_answer": "三天",
        "expected_tool": "search_knowledge", "allowed_categories": ["payment_policy"],
    }
    with pytest.raises(ValueError, match="不存在的知识来源"):
        validate_runtime_batch(original, [result], {"new.md#新政策"})


def test_semantic_review_failure_cannot_count_as_completed_task(monkeypatch):
    from app.modules.knowledge.schemas import AnswerQualityScores

    class Client:
        def __init__(self):
            self.calls = 0

        def invoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(content="", tool_calls=[{
                    "id": "call-one", "name": "search_knowledge", "args": {"query": "退款渠道"},
                }])
            return AIMessage(content="无法确定")

    class ReviewClient:
        def invoke(self, messages):
            return AIMessage(content="不是JSON")

    class Judge:
        model_client = ReviewClient()

        def judge(self, *arguments):
            return AnswerQualityScores(correctness=1, faithfulness=1, completeness=1)

    class Tool:
        def invoke(self, arguments):
            return {"sources": [], "error": {"code": "NO_KNOWLEDGE"}}

    monkeypatch.setattr("scripts.evaluate_rag_runtime.search_knowledge", Tool())
    case = {
        "id": "one", "query": "退款渠道", "category": "payment_policy",
        "expected_tool": "search_knowledge", "parameter_check": "semantic",
        "allowed_categories": ["payment_policy"], "reference_answer": "无法确定",
    }
    result = run_case(Client(), Judge(), case)
    assert result["parameters_correct"] is None
    assert result["evaluation_error"] is not None
    assert result["task_passed"] is False


def test_no_model_response_is_not_a_correct_no_tool_decision():
    class Client:
        def invoke(self, messages):
            raise RuntimeError("服务不可用")

    case = {"id": "one", "query": "你好", "expected_tool": None, "reference_answer": "你好"}
    result = run_case(Client(), object(), case)
    assert result["tool_selection_correct"] is None
    assert result["task_passed"] is False
