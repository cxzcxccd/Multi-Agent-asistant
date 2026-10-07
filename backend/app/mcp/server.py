"""通过Streamable HTTP暴露现有商品工具。"""

from mcp.server.fastmcp import FastMCP
from functools import wraps
from typing import Any
from langchain_core.tools import BaseTool

from app.ai.tools.catalog import get_catalog_tools


def validated_handler(tool: BaseTool) -> Any:
    """保留原函数签名，并执行同一套Pydantic参数校验。"""

    @wraps(tool.func)
    def invoke(**arguments: Any) -> dict[str, Any]:
        return tool.invoke(arguments)

    return invoke


def create_catalog_mcp() -> FastMCP:
    """复用商品工具的参数模型和业务实现，避免两套规则发生偏差。"""

    server = FastMCP(
        "极客优选商品工具",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
    )
    for tool in get_catalog_tools():
        server.add_tool(
            validated_handler(tool),
            name=tool.name,
            description=tool.description,
        )
    return server
