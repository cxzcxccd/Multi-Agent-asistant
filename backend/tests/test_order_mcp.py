"""订单MCP的真实HTTP权限、凭证和并发隔离测试。"""

import asyncio
import socket
import time
from contextlib import asynccontextmanager

import httpx
import jwt
import pytest
import uvicorn
from starlette.applications import Starlette

from app.mcp.auth import (
    OrderMcpAuthentication,
    create_order_token,
    current_mcp_buyer,
    signing_key,
    verify_order_token,
)
from app.mcp.client import OrderMcpClient
from app.mcp.orders import create_order_mcp


def test_order_token_rejects_wrong_audience_scope_and_expiry() -> None:
    token = create_order_token("A")
    assert verify_order_token(token) == "A"
    original = jwt.decode(token, signing_key(), algorithms=["HS256"], audience="commerce-orders-mcp")
    changes = [
        {"aud": "another-service"},
        {"scope": "orders:write"},
        {"exp": int(time.time()) - 1},
        {"sub": "unknown-buyer"},
    ]
    for change in changes:
        payload = dict(original)
        payload.update(change)
        invalid = jwt.encode(payload, signing_key(), algorithm="HS256")
        with pytest.raises(jwt.InvalidTokenError):
            verify_order_token(invalid)
    tampered = jwt.encode(original, b"different-secret-key-at-least-32-bytes", algorithm="HS256")
    with pytest.raises(jwt.InvalidTokenError):
        verify_order_token(tampered)


def test_order_mcp_http_enforces_identity_and_concurrency() -> None:
    async def run() -> None:
        mcp = create_order_mcp()

        @asynccontextmanager
        async def lifespan(_app):
            async with mcp.session_manager.run():
                yield

        app = Starlette(lifespan=lifespan)
        app.mount("/orders-mcp", OrderMcpAuthentication(mcp.streamable_http_app()))
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        server_task = asyncio.create_task(server.serve(sockets=[listener]))
        url = f"http://127.0.0.1:{port}/orders-mcp/"
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if server_task.done():
                        await server_task
                    await asyncio.sleep(0.01)

            async with httpx.AsyncClient(trust_env=False) as http:
                expired_payload = jwt.decode(
                    create_order_token("A"),
                    signing_key(),
                    algorithms=["HS256"],
                    audience="commerce-orders-mcp",
                )
                expired_payload["exp"] = int(time.time()) - 1
                expired_token = jwt.encode(
                    expired_payload,
                    signing_key(),
                    algorithm="HS256",
                )
                invalid_headers = [
                    {},
                    {"Authorization": "Bearer invalid"},
                    {"Authorization": f"Bearer {expired_token}"},
                ]
                for headers in invalid_headers:
                    response = await http.post(url, headers=headers, json={})
                    assert response.status_code == 401

            client = OrderMcpClient(url)
            tools = client.tools()
            assert set(tools[0].args) == set()
            assert set(tools[1].args) == {"order_id"}
            config_a = {"configurable": {"buyer_id": "A"}}
            config_b = {"configurable": {"buyer_id": "B"}}
            results = await asyncio.gather(
                tools[0].ainvoke({}, config=config_a),
                tools[0].ainvoke({}, config=config_b),
            )
            for order in results[0]["orders"]:
                assert order["buyer_id"] == "A"
            for order in results[1]["orders"]:
                assert order["buyer_id"] == "B"
            assert results[0]["total"] > 0
            assert results[1]["total"] > 0

            foreign = await tools[1].ainvoke({"order_id": "20001"}, config=config_a)
            missing = await tools[1].ainvoke({"order_id": "99999"}, config=config_a)
            assert foreign == missing
            detail = await tools[1].ainvoke({"order_id": "10001"}, config=config_a)
            assert detail["order"]["id"] == "10001"
            logistics = await tools[2].ainvoke({"order_id": "10001"}, config=config_a)
            assert logistics["success"] is True
            with pytest.raises(ValueError, match="可信买家身份"):
                await tools[0].ainvoke({})
            assert current_mcp_buyer.get() is None
        finally:
            server.should_exit = True
            await server_task
            listener.close()

    asyncio.run(run())
