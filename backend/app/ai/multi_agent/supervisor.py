"""根据 Query 分析结果为客服请求选择最小领域 Agent 集合。"""

from langchain_core.tools import BaseTool

from app.ai.multi_agent.schemas import AgentName, AgentPlan
from app.ai.query_preprocessor import IntentName, QueryAnalysis
from app.ai.tools.after_sales import get_after_sale_tools
from app.ai.tools.catalog import get_catalog_tools
from app.ai.tools.knowledge import get_knowledge_tools
from app.ai.tools.orders import get_order_tools


INTENT_AGENT_MAP: dict[IntentName, AgentName] = {
    IntentName.PRODUCT_INQUIRY: AgentName.PRODUCT,
    IntentName.ORDER_INQUIRY: AgentName.ORDER,
    IntentName.AFTER_SALE: AgentName.AFTER_SALES,
    IntentName.KNOWLEDGE_INQUIRY: AgentName.KNOWLEDGE,
    IntentName.HUMAN_SERVICE: AgentName.GENERAL,
    IntentName.GENERAL_CONVERSATION: AgentName.GENERAL,
}


AGENT_DESCRIPTIONS: dict[AgentName, str] = {
    AgentName.PRODUCT: "商品 Agent 负责商品搜索、详情、价格、库存和推荐。",
    AgentName.ORDER: "订单 Agent 负责当前买家的订单详情和物流查询。",
    AgentName.AFTER_SALES: "售后 Agent 负责售后记录查询和待确认申请草稿。",
    AgentName.KNOWLEDGE: "知识库 Agent 负责政策、保修和使用说明检索。",
    AgentName.GENERAL: "通用 Agent 负责无需业务工具的普通对话和人工服务引导。",
}


def _agent_tools() -> dict[AgentName, list[BaseTool]]:
    """按领域返回工具白名单，防止 Agent 越权调用其他业务工具。"""

    return {
        AgentName.PRODUCT: get_catalog_tools(),
        AgentName.ORDER: get_order_tools(),
        AgentName.AFTER_SALES: get_after_sale_tools(),
        AgentName.KNOWLEDGE: get_knowledge_tools(),
        AgentName.GENERAL: [],
    }


def tools_for_plan(plan: AgentPlan) -> list[BaseTool]:
    """合并计划内 Agent 的工具，并保持工具名称唯一。"""

    tools_by_agent = _agent_tools()
    selected: list[BaseTool] = []
    seen_names: set[str] = set()

    for agent_name in plan.agents:
        for current_tool in tools_by_agent[agent_name]:
            if current_tool.name in seen_names:
                continue
            selected.append(current_tool)
            seen_names.add(current_tool.name)
    return selected


class Supervisor:
    """使用语义路由结果生成可解释且可测试的 Agent 调度计划。"""

    def plan(self, analysis: QueryAnalysis) -> AgentPlan:
        """选择主 Agent，并为复合 Query 加入必要的协作 Agent。"""

        intents = [analysis.primary_intent, *analysis.secondary_intents]
        agents: list[AgentName] = []

        for intent in intents:
            agent_name = INTENT_AGENT_MAP[intent]
            if agent_name not in agents:
                agents.append(agent_name)

        if len(agents) > 1 and AgentName.GENERAL in agents:
            agents.remove(AgentName.GENERAL)
        if not agents:
            agents.append(AgentName.GENERAL)

        primary_agent = agents[0]
        preliminary_plan = AgentPlan(
            agents=agents,
            primary_agent=primary_agent,
            reason=self._build_reason(agents),
        )
        allowed_tools = [tool.name for tool in tools_for_plan(preliminary_plan)]
        return preliminary_plan.model_copy(update={"allowed_tools": allowed_tools})

    @staticmethod
    def _build_reason(agents: list[AgentName]) -> str:
        descriptions: list[str] = []
        for agent_name in agents:
            descriptions.append(AGENT_DESCRIPTIONS[agent_name])
        return " ".join(descriptions)


def format_agent_context(plan: AgentPlan) -> str:
    """把调度计划转换成模型能够遵守的简短权限说明。"""

    agent_text = "、".join(agent.value for agent in plan.agents)
    tool_text = "、".join(plan.allowed_tools) if plan.allowed_tools else "无"
    return (
        f"本轮由 Supervisor 分配给：{agent_text}。\n"
        f"调度原因：{plan.reason}\n"
        f"本轮只允许调用这些工具：{tool_text}。"
    )
