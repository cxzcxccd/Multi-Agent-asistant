"""供客服模型检索商城规则和使用说明的知识工具。"""

from functools import lru_cache
from typing import Any

from langchain.tools import tool
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from app.db.initialize import initialize_database
from app.db.session import get_session_factory
from app.modules.knowledge.repository import KnowledgeRepository
from app.modules.knowledge.schemas import KnowledgeCategory
from app.modules.knowledge.service import KnowledgeService
from app.modules.knowledge.vector_store import VectorStoreUnavailableError


class KnowledgeSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=2, max_length=200, description="用户关于规则或使用方法的问题")
    category: KnowledgeCategory | None = Field(
        default=None,
        description="可选知识分类；不确定时不要填写",
    )


@lru_cache(maxsize=1)
def get_knowledge_tool_service() -> KnowledgeService:
    initialize_database()
    return KnowledgeService(KnowledgeRepository(get_session_factory()))


@tool("search_knowledge", args_schema=KnowledgeSearchInput)
def search_knowledge(
    query: str,
    category: KnowledgeCategory | None = None,
) -> dict[str, Any]:
    """检索退换货、保修、物流、支付规则和数码产品使用说明。"""

    try:
        result = get_knowledge_tool_service().search(query, category)
    except VectorStoreUnavailableError:
        return {
            "success": False,
            "answer_context": "",
            "sources": [],
            "total": 0,
            "error": {
                "code": "VECTOR_DATABASE_UNAVAILABLE",
                "message": "Milvus 向量数据库暂不可用",
            },
        }
    return {
        "success": bool(result.items),
        "answer_context": "\n\n".join(item.content for item in result.items),
        "sources": [item.model_dump(mode="json") for item in result.items],
        "total": result.total,
        "error": None if result.items else {"code": "NO_KNOWLEDGE", "message": "未检索到可靠资料"},
    }


KNOWLEDGE_TOOLS: tuple[BaseTool, ...] = (search_knowledge,)


def get_knowledge_tools() -> list[BaseTool]:
    return list(KNOWLEDGE_TOOLS)
