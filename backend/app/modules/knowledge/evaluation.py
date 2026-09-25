"""评估知识检索结果与基于知识生成的回答。"""

import json
import re
import time
from pathlib import Path
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from app.ai.model_client import ModelClient
from app.modules.knowledge.schemas import (
    AnswerQualityScores,
    KnowledgeSearchItem,
    RagEvaluationCaseResult,
    RagEvaluationReport,
)
from app.modules.knowledge.service import KnowledgeService


class RagAnswerEngine(Protocol):
    """隔离模型调用，便于使用真实模型和可控测试替身。"""

    def generate(self, question: str, contexts: list[KnowledgeSearchItem]) -> str: ...

    def judge(
        self,
        question: str,
        reference_answer: str,
        contexts: list[KnowledgeSearchItem],
        answer: str,
    ) -> AnswerQualityScores: ...


class ModelRagAnswerEngine:
    """使用项目配置的聊天模型生成答案并进行结构化质量评审。"""

    def __init__(self, model_client: ModelClient) -> None:
        self.model_client = model_client

    def generate(self, question: str, contexts: list[KnowledgeSearchItem]) -> str:
        context_text = self._format_contexts(contexts)
        messages = [
            SystemMessage(
                content=(
                    "你是电商知识库客服。只能依据给定资料回答，不得补充资料外事实。"
                    "资料不足时明确回答‘根据现有知识库无法确定’。"
                    "使用资料时，在相关结论后以 [来源: source] 格式标注来源。"
                )
            ),
            HumanMessage(content=f"用户问题：{question}\n\n知识资料：\n{context_text}"),
        ]
        response = self.model_client.invoke(messages)
        return self._message_text(response.content)

    def judge(
        self,
        question: str,
        reference_answer: str,
        contexts: list[KnowledgeSearchItem],
        answer: str,
    ) -> AnswerQualityScores:
        context_text = self._format_contexts(contexts)
        messages = [
            SystemMessage(
                content=(
                    "你是严格的RAG评测员。仅返回JSON对象，不要输出Markdown。"
                    "correctness衡量回答与参考答案是否一致；faithfulness衡量回答是否完全由资料支持；"
                    "completeness衡量参考答案要点是否覆盖。三个分数范围都是0到1。"
                    "reason用一句中文说明主要依据。"
                )
            ),
            HumanMessage(
                content=(
                    f"问题：{question}\n"
                    f"参考答案：{reference_answer}\n"
                    f"知识资料：\n{context_text}\n\n"
                    f"待评回答：{answer}"
                )
            ),
        ]
        response = self.model_client.invoke(messages)
        raw_text = self._message_text(response.content)
        payload = self._parse_json_object(raw_text)
        return AnswerQualityScores.model_validate(payload)

    @staticmethod
    def _format_contexts(contexts: list[KnowledgeSearchItem]) -> str:
        if not contexts:
            return "（没有检索到可靠资料）"
        blocks: list[str] = []
        for item in contexts:
            blocks.append(f"[来源: {item.source}]\n{item.content}")
        return "\n\n".join(blocks)

    @staticmethod
    def _message_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        return str(content).strip()

    @staticmethod
    def _parse_json_object(raw_text: str) -> dict[str, Any]:
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        payload = json.loads(cleaned)
        if not isinstance(payload, dict):
            raise ValueError("评测模型没有返回 JSON 对象")
        return payload


class RagEvaluator:
    def __init__(
        self,
        service: KnowledgeService,
        cases_path: Path,
        answer_engine: RagAnswerEngine | None = None,
    ) -> None:
        self.service = service
        self.cases_path = cases_path
        self.answer_engine = answer_engine

    def run(self, limit: int = 3) -> RagEvaluationReport:
        cases = self._load_cases()
        results: list[RagEvaluationCaseResult] = []
        recall_values: list[float] = []
        reciprocal_ranks: list[float] = []
        rejection_results: list[bool] = []
        retrieval_latencies: list[float] = []
        answer_latencies: list[float] = []
        answer_scores: list[AnswerQualityScores] = []
        citation_precisions: list[float] = []

        for case in cases:
            started = time.perf_counter()
            response = self.service.search(case["question"], case.get("category"), limit)
            retrieval_latency = (time.perf_counter() - started) * 1000
            retrieval_latencies.append(retrieval_latency)
            retrieved_sources = [item.source for item in response.items]
            expected_sources = case["expected_sources"]
            first_rank = self._first_relevant_rank(expected_sources, retrieved_sources)

            if expected_sources:
                matched = set(expected_sources) & set(retrieved_sources)
                recall_values.append(len(matched) / len(expected_sources))
                reciprocal_ranks.append(0.0 if first_rank is None else 1 / first_rank)
                retrieval_passed = bool(matched)
            else:
                rejected = len(retrieved_sources) == 0
                rejection_results.append(rejected)
                retrieval_passed = rejected

            generated_answer: str | None = None
            quality: AnswerQualityScores | None = None
            cited_sources: list[str] = []
            citation_precision: float | None = None
            answer_latency: float | None = None
            error_type: str | None = None

            if self.answer_engine is not None:
                answer_started = time.perf_counter()
                generated_answer = self.answer_engine.generate(case["question"], response.items)
                answer_latency = (time.perf_counter() - answer_started) * 1000
                answer_latencies.append(answer_latency)
                cited_sources = self._extract_citations(generated_answer)
                citation_precision = self._citation_precision(cited_sources, response.items)
                if expected_sources:
                    citation_precisions.append(citation_precision)
                quality = self.answer_engine.judge(
                    case["question"], case["reference_answer"], response.items, generated_answer
                )
                answer_scores.append(quality)
                error_type = self._classify_error(retrieval_passed, quality)

            results.append(
                RagEvaluationCaseResult(
                    id=case["id"],
                    question=case["question"],
                    expected_sources=expected_sources,
                    retrieved_sources=retrieved_sources,
                    first_relevant_rank=first_rank,
                    retrieval_passed=retrieval_passed,
                    passed=retrieval_passed,
                    retrieval_latency_ms=round(retrieval_latency, 2),
                    reference_answer=case.get("reference_answer"),
                    generated_answer=generated_answer,
                    cited_sources=cited_sources,
                    citation_precision=citation_precision,
                    answer_latency_ms=None if answer_latency is None else round(answer_latency, 2),
                    answer_quality=quality,
                    error_type=error_type,
                )
            )

        return RagEvaluationReport(
            cases=len(results),
            answer_cases=len(answer_scores),
            recall_at_k=self._average(recall_values),
            mrr=self._average(reciprocal_ranks),
            rejection_accuracy=self._average([float(value) for value in rejection_results]),
            average_latency_ms=self._average(retrieval_latencies),
            retrieval_latency_p50_ms=self._percentile(retrieval_latencies, 0.50),
            retrieval_latency_p95_ms=self._percentile(retrieval_latencies, 0.95),
            average_answer_latency_ms=self._average(answer_latencies),
            answer_correctness=self._score_average(answer_scores, "correctness"),
            answer_faithfulness=self._score_average(answer_scores, "faithfulness"),
            answer_completeness=self._score_average(answer_scores, "completeness"),
            citation_precision=self._average(citation_precisions),
            results=results,
        )

    def save(self, report: RagEvaluationReport, output_directory: Path) -> tuple[Path, Path]:
        """保存逐条结果和便于阅读的汇总报告。"""

        output_directory.mkdir(parents=True, exist_ok=True)
        detail_path = output_directory / "evaluation_results.jsonl"
        report_path = output_directory / "evaluation_report.md"
        detail_lines: list[str] = []
        for result in report.results:
            detail_lines.append(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
        detail_path.write_text("\n".join(detail_lines) + "\n", encoding="utf-8")
        report_path.write_text(self._markdown_report(report), encoding="utf-8")
        return detail_path, report_path

    def _load_cases(self) -> list[dict[str, Any]]:
        cases = json.loads(self.cases_path.read_text(encoding="utf-8"))
        if not isinstance(cases, list):
            raise ValueError("RAG 评测数据必须是 JSON 数组")
        for case in cases:
            if self.answer_engine is not None and not case.get("reference_answer"):
                raise ValueError(f"完整评测案例 {case.get('id')} 缺少 reference_answer")
        return list(cases)

    @staticmethod
    def _extract_citations(answer: str) -> list[str]:
        return re.findall(r"\[来源:\s*([^\]]+)]", answer)

    @staticmethod
    def _citation_precision(citations: list[str], contexts: list[KnowledgeSearchItem]) -> float:
        if not citations:
            return 0.0
        available_sources = {item.source for item in contexts}
        valid_citations = sum(source in available_sources for source in citations)
        return round(valid_citations / len(citations), 4)

    @staticmethod
    def _classify_error(retrieval_passed: bool, quality: AnswerQualityScores) -> str | None:
        if not retrieval_passed:
            return "retrieval_error"
        if quality.faithfulness < 0.7:
            return "faithfulness_error"
        if quality.correctness < 0.7 or quality.completeness < 0.7:
            return "generation_error"
        return None

    @staticmethod
    def _first_relevant_rank(expected: list[str], retrieved: list[str]) -> int | None:
        for index, source in enumerate(retrieved, start=1):
            if source in expected:
                return index
        return None

    @staticmethod
    def _average(values: list[float]) -> float:
        if not values:
            return 0.0
        return round(sum(values) / len(values), 4)

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = round((len(ordered) - 1) * percentile)
        return round(ordered[index], 2)

    @staticmethod
    def _score_average(scores: list[AnswerQualityScores], field: str) -> float:
        values = [float(getattr(score, field)) for score in scores]
        return RagEvaluator._average(values)

    @staticmethod
    def _markdown_report(report: RagEvaluationReport) -> str:
        lines = [
            "# RAG 检索与回答评测报告",
            "",
            f"- 案例数：{report.cases}",
            f"- 回答评测案例数：{report.answer_cases}",
            f"- Recall@K：{report.recall_at_k:.4f}",
            f"- MRR：{report.mrr:.4f}",
            f"- 拒答准确率：{report.rejection_accuracy:.4f}",
            f"- 检索延迟 P50 / P95：{report.retrieval_latency_p50_ms:.2f} / {report.retrieval_latency_p95_ms:.2f} ms",
            f"- 回答正确性：{report.answer_correctness:.4f}",
            f"- 回答忠实度：{report.answer_faithfulness:.4f}",
            f"- 回答完整性：{report.answer_completeness:.4f}",
            f"- 引用准确率：{report.citation_precision:.4f}",
            "",
            "## 失败案例",
            "",
        ]
        failures = [result for result in report.results if result.error_type]
        if not failures:
            lines.append("没有检测到失败案例。")
        for result in failures:
            lines.append(f"- `{result.id}` {result.error_type}：{result.question}")
        lines.append("")
        return "\n".join(lines)
