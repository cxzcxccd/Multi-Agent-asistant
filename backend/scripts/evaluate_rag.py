"""通过真实 BGE、Milvus 和聊天模型运行 RAG 评测。"""

import argparse
from pathlib import Path

from app.ai.model_client import create_model_client
from app.db.initialize import initialize_database
from app.db.session import get_session_factory
from app.modules.knowledge.evaluation import (
    ModelRagAnswerEngine,
    RagComparisonEvaluator,
    RagEvaluator,
)
from app.modules.knowledge.embeddings import create_embedding_provider
from app.modules.knowledge.indexer import KnowledgeIndexer
from app.modules.knowledge.loader import load_knowledge_directory
from app.modules.knowledge.reranker import FastEmbedReranker
from app.modules.knowledge.repository import KnowledgeRepository
from app.modules.knowledge.service import KnowledgeService
from app.modules.knowledge.vector_store import InMemoryKnowledgeVectorStore
from app.modules.knowledge.records import DEFAULT_CASES_PATH, new_record_directory

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="评估项目的 RAG 检索与回答效果")
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="只评估检索，不调用聊天模型生成和评审答案",
    )
    parser.add_argument("--limit", type=int, default=5, help="每个问题返回的知识块数量")
    parser.add_argument("--strategy", choices=["keyword", "vector", "hybrid", "hybrid_rerank", "hyde"],
                        default="hybrid", help="单方案检索策略；HyDE 仍会调用模型生成假设文档")
    parser.add_argument("--include-hyde", action="store_true", help="对比评测额外包含 HyDE（调用模型）")
    parser.add_argument(
        "--compare",
        action="store_true",
        help="比较关键词、向量、混合和混合重排四种检索方案",
    )
    parser.add_argument(
        "--use-category",
        action="store_true",
        help="使用数据中的分类过滤检索；默认关闭，避免向检索器泄露答案类别",
    )
    parser.add_argument(
        "--in-memory",
        action="store_true",
        help="使用真实BGE和内存余弦检索运行离线评测，不连接Milvus",
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help="评测案例 JSON 文件",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="评测结果目录",
    )
    parser.add_argument("--input-price-per-million", type=float, help="每百万输入Token价格，由用户提供")
    parser.add_argument("--output-price-per-million", type=float, help="每百万输出Token价格，由用户提供")
    parser.add_argument("--cost-currency", default="CNY")
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    if arguments.output is None:
        arguments.output = new_record_directory()
    initialize_database()
    repository = KnowledgeRepository(get_session_factory())
    if arguments.in_memory:
        embedding_provider = create_embedding_provider()
        vector_store = InMemoryKnowledgeVectorStore("rag_evaluation_in_memory")
        chunks = load_knowledge_directory(BACKEND_ROOT / "data" / "knowledge")
        indexer = KnowledgeIndexer(repository, embedding_provider, vector_store)
        indexer.rebuild(chunks)
        service = KnowledgeService(
            repository=repository,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
        )
        print("评测向量存储：in-memory（真实BGE，未连接Milvus）")
    else:
        service = KnowledgeService(repository)
    if arguments.strategy == "hybrid_rerank" and not arguments.compare:
        service.reranker = FastEmbedReranker()

    answer_engine = None
    if not arguments.retrieval_only:
        model_client = create_model_client(tools=[])
        answer_engine = ModelRagAnswerEngine(model_client)

    evaluator = RagEvaluator(
        service,
        arguments.cases,
        answer_engine,
        strategy=arguments.strategy,
        use_category=arguments.use_category,
        input_price_per_million=arguments.input_price_per_million,
        output_price_per_million=arguments.output_price_per_million,
        cost_currency=arguments.cost_currency,
    )
    if arguments.compare and arguments.input_price_per_million is not None:
        raise ValueError("费用实验请逐方案运行，比较入口暂不支持价格参数")
    if arguments.compare:
        rerank_service = KnowledgeService(
            repository=repository,
            embedding_provider=service.embedding_provider,
            vector_store=service.vector_store,
            minimum_score=service.minimum_score,
            bm25_minimum_score=service.bm25_minimum_score,
            rrf_rank_constant=service.rrf_rank_constant,
            reranker=FastEmbedReranker(),
        )
        services = {
            "keyword": service,
            "vector": service,
            "hybrid": service,
            "hybrid_rerank": rerank_service,
        }
        if arguments.include_hyde:
            services["hyde"] = service
        comparison_evaluator = RagComparisonEvaluator(
            services,
            arguments.cases,
            answer_engine,
            arguments.use_category,
        )
        comparison = comparison_evaluator.run()
        comparison_path = comparison_evaluator.save(comparison, arguments.output)
        for item in comparison.reports:
            print(
                f"{item.strategy}: Recall@3={item.recall_at_3:.4f}, "
                f"MRR={item.mrr:.4f}, P95={item.retrieval_latency_p95_ms:.2f}ms"
            )
        print(f"对比报告: {comparison_path}")
        return

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
