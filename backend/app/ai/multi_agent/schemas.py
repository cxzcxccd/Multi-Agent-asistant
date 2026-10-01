"""多 Agent 调度过程使用的稳定数据格式。"""

from enum import StrEnum
from typing import Any, Literal
from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AgentName(StrEnum):
    """可以由 Supervisor 调度的领域 Agent。"""

    PRODUCT = "product_agent"
    ORDER = "order_agent"
    AFTER_SALES = "after_sales_agent"
    KNOWLEDGE = "knowledge_agent"
    GENERAL = "general_agent"
    SYNTHESIS = "synthesis_agent"


class AgentTask(BaseModel):
    """Supervisor 拆分出的一项可独立执行的领域任务。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=50)
    agent_name: AgentName
    description: str = Field(min_length=1, max_length=500)
    depends_on: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)


class AgentTaskResult(BaseModel):
    """领域 Agent 执行结束后交给汇总 Agent 的结构化结果。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    agent_name: AgentName
    status: Literal["completed", "failed", "skipped"]
    summary: str
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    tool_rounds: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    error: str | None = None


class AgentPlan(BaseModel):
    """一次请求的确定性调度计划。"""

    model_config = ConfigDict(extra="forbid")

    agents: list[AgentName] = Field(min_length=1)
    primary_agent: AgentName
    reason: str = Field(min_length=1)
    allowed_tools: list[str] = Field(default_factory=list)
    tasks: list[AgentTask] = Field(default_factory=list)
    execution_mode: Literal["sequential", "parallel"] = "sequential"


class AgentRunRecord(BaseModel):
    """一次多 Agent 运行的持久化快照。"""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    conversation_id: UUID
    buyer_id: str
    status: Literal["completed", "partial", "failed"]
    plan: AgentPlan
    results: list[AgentTaskResult]
    final_answer: str | None = None
    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    started_at: datetime
    completed_at: datetime
