"""API 总路由。"""

from fastapi import APIRouter

from app.modules.catalog.router import router as catalog_router

api_router = APIRouter()
api_router.include_router(catalog_router)


@api_router.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    return {"status": "ok", "phase": "backend-scaffold"}
