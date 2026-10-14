"""API 总路由。"""

from fastapi import APIRouter
from app.core.config import settings
from app.modules.auth.dependencies import StaffPrincipal

from app.modules.after_sales.router import router as after_sales_router
from app.modules.auth.router import router as auth_router
from app.modules.catalog.router import router as catalog_router
from app.modules.conversations.router import router as conversation_router
from app.modules.knowledge.router import router as knowledge_router
from app.modules.knowledge.router import staff_router as staff_knowledge_router
from app.modules.orders.router import router as order_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(catalog_router)
api_router.include_router(conversation_router)
api_router.include_router(order_router)
api_router.include_router(after_sales_router)
api_router.include_router(knowledge_router)
api_router.include_router(staff_knowledge_router)


@api_router.get("/staff/runtime/status", tags=["system"])
async def runtime_status(_principal: StaffPrincipal) -> dict:
    """提供配置状态供历史执行记录展示，不返回密钥，也不冒充连通性检测。"""
    return {
        "model_name": settings.model_name or None,
        "model_configured": bool(settings.model_name and settings.model_api_key and settings.model_api_key.get_secret_value()),
        "mcp_enabled": [name for name, enabled in (
            ("catalog", settings.catalog_mcp_enabled), ("orders", settings.order_mcp_enabled),
        ) if enabled],
    }


@api_router.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    return {"status": "ok", "phase": "backend-scaffold"}
