"""真实用量的聚合、缺失标记和 MCP 接入统计。"""

import asyncio
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool
from app.ai.telemetry import measure_model, summarize
from app.ai.multi_agent.schemas import AgentTaskResult, AgentName
from app.ai.multi_agent.runtime import MultiAgentRuntime
from app.ai.query_preprocessor import IntentName
from app.ai.tools.catalog import GetProductInput
from tests.test_multi_agent_runtime import FixedPreprocessor, SynthesisClient
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.modules.auth.schemas import Principal
from app.modules.auth.security import create_access_token


def test_usage_aggregation_does_not_guess_missing_tokens():
    known = measure_model(AIMessage(content="回复", usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}), "agent", "fixture-model")
    unknown = measure_model(AIMessage(content="未报告"), "synthesis", "fixture-model")
    result = summarize("fixture-model", [known, unknown], [], ["catalog"], 2)
    assert result.token_usage.total_tokens == 120
    assert result.usage_calls == 1
    assert result.model_calls == 2
    assert summarize("fixture-model", [unknown], [], [], 1).token_usage is None
    assert summarize("fixture-model", [], [], [], 0).token_usage.total_tokens == 0


def test_native_metadata_and_mcp_results_are_measured():
    usage = measure_model(AIMessage(content="回复", response_metadata={
        "model_name": "provider-model", "token_usage": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12},
    }), "synthesis")
    task = AgentTaskResult(task_id="product", agent_name="product_agent", status="completed", summary="结果", tool_results=[
        {"name": "search_products", "transport": "mcp", "server": "catalog", "output": {"success": True}},
        {"name": "search_products", "transport": "mcp", "server": "catalog", "output": {"success": False}},
        {"name": "get_product", "output": {"success": True}},
    ])
    result = summarize(None, [usage], [task], ["catalog"], 1)
    assert result.model_name == "provider-model"
    assert result.token_usage.total_tokens == 12
    assert result.mcp_calls[0].calls == 2
    assert result.mcp_calls[0].successes == 1


def test_runtime_returns_model_usage_and_actual_mcp_transport():
    class Client:
        calls = 0
        async def ainvoke(self, messages, config=None):
            self.calls += 1
            return AIMessage(content="" if self.calls == 1 else "已查询商品", tool_calls=[{
                "id": "get", "name": "get_product", "args": {"product_id": "p01"},
            }] if self.calls == 1 else [], usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}, response_metadata={"model_name": "provider-model"})

    async def get_product(product_id: str):
        return {"success": True, "product": {"id": product_id, "name": "耳机"}}

    async def scenario():
        tool = StructuredTool.from_function(name="get_product", description="查询商品", coroutine=get_product,
            args_schema=GetProductInput, metadata={"transport": "mcp", "server": "catalog"})
        runtime = MultiAgentRuntime(model_client=object(), query_preprocessor=FixedPreprocessor(IntentName.PRODUCT_INQUIRY),
            agent_clients={AgentName.PRODUCT: Client()}, synthesis_client=SynthesisClient("推荐耳机"), catalog_tools=[tool])
        result = await runtime.ainvoke([HumanMessage(content="了解耳机")])
        assert result.telemetry.token_usage.total_tokens == 30
        assert result.telemetry.usage_calls == 2
        assert result.telemetry.model_calls == 3
        assert result.telemetry.mcp_calls[0].tool == "get_product"
        assert result.telemetry.mcp_calls[0].calls == result.telemetry.mcp_calls[0].successes == 1
        assert result.task_results[0].tool_results[0]["transport"] == "mcp"
    asyncio.run(scenario())


def test_runtime_configuration_requires_staff_and_never_exposes_credentials():
    async def scenario():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/staff/runtime/status")).status_code == 401
            buyer = create_access_token(Principal(subject="buyer_a", display_name="买家", role="buyer", buyer_id="A"))
            assert (await client.get("/api/staff/runtime/status", headers={"Authorization": f"Bearer {buyer}"})).status_code == 403
            staff = create_access_token(Principal(subject="staff", display_name="客服", role="staff"))
            response = await client.get("/api/staff/runtime/status", headers={"Authorization": f"Bearer {staff}"})
            assert response.status_code == 200
            assert set(response.json()) == {"model_name", "model_configured", "mcp_enabled"}
    asyncio.run(scenario())
