"""多 Agent 调度过程使用的稳定数据格式。"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AgentName(StrEnum):
    """可以由 Supervisor 调度的领域 Agent。"""

    PRODUCT = "product_agent"
    ORDER = "order_agent"
    AFTER_SALES = "after_sales_agent"
    KNOWLEDGE = "knowledge_agent"
    GENERAL = "general_agent"


class AgentPlan(BaseModel):
    """一次请求的确定性调度计划。"""

    model_config = ConfigDict(extra="forbid")

    agents: list[AgentName] = Field(min_length=1)
    primary_agent: AgentName
    reason: str = Field(min_length=1)
    allowed_tools: list[str] = Field(default_factory=list)
