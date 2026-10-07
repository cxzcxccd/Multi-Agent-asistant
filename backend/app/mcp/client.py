"""把MCP商品调用适配为领域Agent可以执行的LangChain工具。"""

import asyncio
import json
from typing import Any

import httpx
from langchain_core.tools import BaseTool, StructuredTool
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.ai.tools.catalog import get_catalog_tools


class CatalogMcpClient:
    """每次调用建立独立会话，避免并发任务共享可变协议状态。"""

    def __init__(self, url: str, timeout_seconds: float = 15) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """初始化协议、确认远端工具存在并执行调用。"""

        async with asyncio.timeout(self.timeout_seconds):
            async with httpx.AsyncClient(trust_env=False) as http_client:
                async with streamable_http_client(
                    self.url,
                    http_client=http_client,
                ) as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        await session.initialize()
                        available = await session.list_tools()
                        names = set()
                        for tool in available.tools:
                            names.add(tool.name)
                        result = None
                        if name in names:
                            result = await session.call_tool(name, arguments)

        # 在协议上下文关闭后处理业务错误，避免AnyIO将它包装成异常组。
        if result is None:
            raise RuntimeError(f"MCP服务没有提供工具：{name}")
        if result.isError:
            raise RuntimeError("MCP商品工具执行失败")
        if result.structuredContent is not None:
            return result.structuredContent
        for content in result.content:
            if content.type == "text":
                output = json.loads(content.text)
                if isinstance(output, dict):
                    return output
        raise RuntimeError("MCP商品工具没有返回结构化结果")

    def tools(self) -> list[BaseTool]:
        """沿用本地参数模型及名称，使原有白名单和参数校验继续有效。"""

        tools: list[BaseTool] = []
        for local_tool in get_catalog_tools():
            coroutine = self._coroutine_for(local_tool.name)
            tools.append(
                StructuredTool.from_function(
                    coroutine=coroutine,
                    name=local_tool.name,
                    description=local_tool.description,
                    args_schema=local_tool.args_schema,
                )
            )
        return tools

    def _coroutine_for(self, name: str) -> Any:
        async def invoke(**arguments: Any) -> dict[str, Any]:
            return await self.call(name, arguments)

        return invoke
