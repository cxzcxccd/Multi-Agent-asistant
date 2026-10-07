"""真实HTTP上的MCP发现、商品调用、校验和适配测试。"""

import asyncio
import socket
from contextlib import asynccontextmanager

import pytest
import uvicorn
from starlette.applications import Starlette

from app.mcp.client import CatalogMcpClient
from app.mcp.server import create_catalog_mcp


def test_catalog_mcp_http_round_trip() -> None:
    """使用真实SDK和临时HTTP端口，不需要真实模型或外部服务。"""

    async def run() -> None:
        mcp = create_catalog_mcp()

        @asynccontextmanager
        async def lifespan(_app):
            async with mcp.session_manager.run():
                yield

        app = Starlette(lifespan=lifespan)
        app.mount("/mcp", mcp.streamable_http_app())
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        config = uvicorn.Config(app, log_level="error")
        server = uvicorn.Server(config)
        server_task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if server_task.done():
                        await server_task
                    await asyncio.sleep(0.01)

            client = CatalogMcpClient(f"http://127.0.0.1:{port}/mcp/")
            tools = client.tools()
            assert [tool.name for tool in tools] == [
                "search_products",
                "get_product",
            ]
            result = await tools[0].ainvoke({"category": "耳机", "max_price": 300})
            assert result["success"] is True
            assert result["returned"] > 0
            product = await tools[1].ainvoke({"product_id": "p01"})
            assert product["product"]["id"] == "p01"
            missing = await client.call("get_product", {"product_id": "p99"})
            assert missing["success"] is False
            with pytest.raises(RuntimeError, match="执行失败"):
                await client.call("search_products", {"limit": 11})
            with pytest.raises(RuntimeError, match="没有提供工具"):
                await client.call("delete_order", {})
        finally:
            server.should_exit = True
            await server_task
            listener.close()

    asyncio.run(run())
