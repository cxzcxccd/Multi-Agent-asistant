"""通过真实 BGE、Milvus 和聊天模型运行 RAG 评测。"""

import argparse
from pathlib import Path

from app.ai.model_client import create_model_client
from app.db.initialize import initialize_database
from app.db.session import get_session_factory
from app.modules.knowledge.evaluation import ModelRagAnswerEngine, RagEvaluator
from app.modules.knowledge.repository import KnowledgeRepository
from app.modules.knowledge.service import KnowledgeService

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="评估项目的 RAG 检索与回答效果")
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="只评估检索，不调用聊天模型生成和评审答案",
    )
    parser.add_argument("--limit", type=int, default=3, help="每个问题返回的知识块数量")
    parser.add_argument(
        "--cases",
        type=Path,
        default=BACKEND_ROOT / "data" / "evaluation" / "rag_cases.json",
        help="评测案例 JSON 文件",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=BACKEND_ROOT / "data" / "evaluation" / "results",
        help="评测结果目录",
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    initialize_database()
    repository = KnowledgeRepository(get_session_factory())
    service = KnowledgeService(repository)

    answer_engine = None
    if not arguments.retrieval_only:
        model_client = create_model_client(tools=[])
        answer_engine = ModelRagAnswerEngine(model_client)

    evaluator = RagEvaluator(service, arguments.cases, answer_engine)
    report = evaluator.run(limit=arguments.limit)
    detail_path, report_path = evaluator.save(report, arguments.output)

    print(f"评测完成：{report.cases} 条")
    print(f"Recall@{arguments.limit}: {report.recall_at_k:.4f}")
    print(f"MRR: {report.mrr:.4f}")
    print(f"回答正确性: {report.answer_correctness:.4f}")
    print(f"回答忠实度: {report.answer_faithfulness:.4f}")
    print(f"明细: {detail_path}")
    print(f"报告: {report_path}")


if __name__ == "__main__":
    main()
