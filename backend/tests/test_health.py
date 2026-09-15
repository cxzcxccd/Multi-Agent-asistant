"""FastAPI 应用骨架的冒烟测试。"""

import asyncio

from httpx import ASGITransport, AsyncClient

from app.main import app


def test_health_check() -> None:
    async def request_health() -> tuple[int, dict[str, str]]:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")
            return response.status_code, response.json()

    status_code, body = asyncio.run(request_health())

    assert status_code == 200
    assert body == {"status": "ok", "phase": "backend-scaffold"}


def test_frontend_origin_is_allowed_by_cors() -> None:
    """确认本地前端可以跨端口调用后端接口。"""

    async def request_preflight() -> tuple[int, str | None]:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.options(
                "/api/chat",
                headers={
                    "Origin": "http://127.0.0.1:5173",
                    "Access-Control-Request-Method": "POST",
                },
            )
            return response.status_code, response.headers.get(
                "access-control-allow-origin"
            )

    status_code, allowed_origin = asyncio.run(request_preflight())

    assert status_code == 200
    assert allowed_origin == "http://127.0.0.1:5173"
