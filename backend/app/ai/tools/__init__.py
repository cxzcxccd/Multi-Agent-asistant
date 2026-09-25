"""汇总可以绑定给客服模型的业务工具。"""

from langchain_core.tools import BaseTool

from app.ai.tools.after_sales import get_after_sale_tools
from app.ai.tools.catalog import get_catalog_tools
from app.ai.tools.knowledge import get_knowledge_tools
from app.ai.tools.orders import get_order_tools


def get_customer_service_tools() -> list[BaseTool]:
    """按稳定顺序返回商品、订单、物流和售后工具。"""

    tools: list[BaseTool] = []
    tools.extend(get_catalog_tools())
    tools.extend(get_order_tools())
    tools.extend(get_after_sale_tools())
    tools.extend(get_knowledge_tools())
    return tools
