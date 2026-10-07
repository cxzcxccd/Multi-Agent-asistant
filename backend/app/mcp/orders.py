"""受保护的订单MCP工具；买家身份不会出现在模型参数中。"""

from mcp.server.fastmcp import FastMCP
from langchain_core.runnables import RunnableConfig

from app.ai.tools.orders import get_order, get_logistics, list_orders
from app.mcp.auth import current_mcp_buyer


def buyer_config() -> RunnableConfig:
    """将已验证的请求身份注入原订单工具的运行配置。"""
    buyer_id = current_mcp_buyer.get()
    if buyer_id not in {"A", "B"}:
        raise RuntimeError("订单MCP缺少已认证买家身份")
    return {"configurable": {"buyer_id": buyer_id}}


def create_order_mcp() -> FastMCP:
    """注册只接受订单参数的三个查询工具。"""
    server = FastMCP(
        "极客优选订单工具",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
    )

    @server.tool(name="list_orders")
    def list_buyer_orders() -> dict:
        """列出当前已认证买家的订单。"""
        return list_orders.invoke({}, config=buyer_config())

    @server.tool(name="get_order")
    def get_buyer_order(order_id: str) -> dict:
        """查询当前已认证买家的订单详情。"""
        return get_order.invoke({"order_id": order_id}, config=buyer_config())

    @server.tool(name="get_logistics")
    def get_buyer_logistics(order_id: str) -> dict:
        """查询当前已认证买家的物流轨迹。"""
        return get_logistics.invoke({"order_id": order_id}, config=buyer_config())

    return server
