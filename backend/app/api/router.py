"""API 总路由。"""

from fastapi import APIRouter

api_router = APIRouter()


@api_router.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    return {"status": "ok", "phase": "backend-scaffold"}
