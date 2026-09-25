"""会话和聊天 FastAPI 接口测试。"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from uuid import UUID

from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, BaseMessage

from app.ai.runtime import RuntimeResult, RuntimeStreamEvent
from app.ai.query_preprocessor import create_fallback_query_preprocessor
from app.main import app
from app.modules.auth.schemas import Principal
from app.modules.auth.security import create_access_token
from app.modules.conversations.router import get_conversation_service
from app.modules.conversations.schemas import ConversationMode
from app.modules.conversations.service import ConversationService


BUYER_TOKEN = create_access_token(
    Principal(subject="buyer_a", display_name="林同学", role="buyer", buyer_id="A")
)
BUYER_B_HEADERS = {
    "Authorization": "Bearer "
    + create_access_token(
        Principal(subject="buyer_b", display_name="陈同学", role="buyer", buyer_id="B")
    )
}
STAFF_HEADERS = {
    "Authorization": "Bearer "
    + create_access_token(Principal(subject="staff", display_name="客服小周", role="staff"))
}


class ReplyRuntime:
    """生成固定回复，避免接口测试请求真实模型。"""

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        config: dict[str, object] | None = None,
    ) -> RuntimeResult:
        reply = AIMessage(content=f"收到：{messages[-1].content}")
        query_analysis = create_fallback_query_preprocessor().analyze(messages)
        return RuntimeResult(
            reply=reply,
            messages=tuple([*messages, reply]),
            model_calls=1,
            tool_rounds=0,
            tool_calls=0,
            query_analysis=query_analysis,
        )

    async def astream(
        self,
        messages: Sequence[BaseMessage],
        config: dict[str, object] | None = None,
    ) -> AsyncIterator[RuntimeStreamEvent]:
        """按两个文本片段返回一轮完整的模型运行。"""

        result = await self.ainvoke(messages, config=config)
        yield RuntimeStreamEvent(type="router_start")
        yield RuntimeStreamEvent(
            type="router_end",
            query_analysis=result.query_analysis,
        )
        yield RuntimeStreamEvent(type="model_start")
        yield RuntimeStreamEvent(type="delta", text="收到：")
        yield RuntimeStreamEvent(
            type="delta",
            text=str(messages[-1].content),
        )
        yield RuntimeStreamEvent(type="complete", result=result)


@asynccontextmanager
async def conversation_client() -> AsyncIterator[
    tuple[AsyncClient, ConversationService]
]:
    """使用独立内存服务请求应用，并在结束后清理依赖覆盖。"""

    service = ConversationService(runtime=ReplyRuntime())
    app.dependency_overrides[get_conversation_service] = lambda: service
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {BUYER_TOKEN}"},
        ) as client:
            yield client, service
    finally:
        app.dependency_overrides.pop(get_conversation_service, None)


def test_chat_creates_and_continues_a_conversation() -> None:
    async def run_scenario() -> None:
        async with conversation_client() as (client, _service):
            created = await client.post(
                "/api/chat",
                json={"buyer_id": "A", "message": "推荐一款耳机"},
            )

            assert created.status_code == 200
            created_body = created.json()
            conversation_id = created_body["conversation_id"]
            assert created_body["assistant_message"]["content"] == (
                "收到：推荐一款耳机"
            )
            assert created_body["run"]["model_calls"] == 1
            assert created_body["run"]["tool_rounds"] == 0
            assert created_body["run"]["tool_calls"] == 0
            assert created_body["run"]["query_analysis"]["primary_intent"] == (
                "product_inquiry"
            )

            continued = await client.post(
                "/api/chat",
                json={
                    "buyer_id": "A",
                    "conversation_id": conversation_id,
                    "message": "预算三百元",
                },
            )

            assert continued.status_code == 200
            assert continued.json()["conversation_id"] == conversation_id

            detail = await client.get(
                f"/api/conversations/{conversation_id}",
                params={"buyer_id": "A"},
            )
            assert detail.status_code == 200
            assert [message["content"] for message in detail.json()["messages"]] == [
                "推荐一款耳机",
                "收到：推荐一款耳机",
                "预算三百元",
                "收到：预算三百元",
            ]

    asyncio.run(run_scenario())


def test_chat_stream_returns_named_sse_events_and_saves_messages() -> None:
    async def run_scenario() -> None:
        async with conversation_client() as (client, service):
            response = await client.post(
                "/api/chat/stream",
                json={"buyer_id": "A", "message": "你是谁"},
            )

            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert "event: start\n" in response.text
            assert '"phase":"router"' in response.text
            assert '"primary_intent":"general_conversation"' in response.text
            assert response.text.count("event: delta\n") == 2
            assert "event: complete\n" in response.text

            conversations = service.list_conversations("A")
            assert len(conversations) == 1
            assert [message.content for message in conversations[0].messages] == [
                "你是谁",
                "收到：你是谁",
            ]

    asyncio.run(run_scenario())


def test_handoff_staff_reply_restore_ai_and_close() -> None:
    async def run_scenario() -> None:
        async with conversation_client() as (client, _) :
            handoff = await client.post(
                "/api/handoff",
                json={"buyer_id": "A", "title": "需要人工帮助"},
            )
            assert handoff.status_code == 200
            conversation_id = handoff.json()["id"]
            assert handoff.json()["mode"] == "waiting"

            takeover = await client.post(
                f"/api/staff/conversations/{conversation_id}/mode",
                json={"staff_id": "客服小周", "mode": "human"},
                headers=STAFF_HEADERS,
            )
            assert takeover.status_code == 200
            assert takeover.json()["mode"] == "human"

            reply = await client.post(
                f"/api/staff/conversations/{conversation_id}/messages",
                json={"staff_id": "客服小周", "message": "你好，我来协助处理。"},
                headers=STAFF_HEADERS,
            )
            assert reply.status_code == 200
            assert reply.json()["messages"][-1]["role"] == "staff"

            restore = await client.post(
                f"/api/staff/conversations/{conversation_id}/mode",
                json={"staff_id": "客服小周", "mode": "ai"},
                headers=STAFF_HEADERS,
            )
            assert restore.status_code == 200
            assert restore.json()["mode"] == "ai"

            takeover_again = await client.post(
                f"/api/staff/conversations/{conversation_id}/mode",
                json={"staff_id": "客服小周", "mode": "human"},
                headers=STAFF_HEADERS,
            )
            assert takeover_again.status_code == 200

            closed = await client.post(
                f"/api/staff/conversations/{conversation_id}/mode",
                json={"staff_id": "客服小周", "mode": "closed"},
                headers=STAFF_HEADERS,
            )
            assert closed.status_code == 200
            assert closed.json()["mode"] == "closed"

    asyncio.run(run_scenario())


def test_conversation_list_is_isolated_by_buyer() -> None:
    async def run_scenario() -> None:
        async with conversation_client() as (client, _service):
            for buyer_id in ("A", "B"):
                headers = None
                if buyer_id == "B":
                    headers = BUYER_B_HEADERS
                response = await client.post(
                    "/api/chat",
                    json={"buyer_id": buyer_id, "message": f"{buyer_id} 的咨询"},
                    headers=headers,
                )
                assert response.status_code == 200

            buyer_a = await client.get(
                "/api/conversations",
                params={"buyer_id": "A"},
            )
            assert buyer_a.status_code == 200
            assert len(buyer_a.json()) == 1
            assert buyer_a.json()[0]["buyer_id"] == "A"

    asyncio.run(run_scenario())


def test_conversation_endpoints_return_expected_errors() -> None:
    async def run_scenario() -> None:
        async with conversation_client() as (client, _service):
            created = await client.post(
                "/api/chat",
                json={"buyer_id": "A", "message": "你好"},
            )
            conversation_id = created.json()["conversation_id"]

            forbidden = await client.get(
                f"/api/conversations/{conversation_id}",
                params={"buyer_id": "B"},
                headers=BUYER_B_HEADERS,
            )
            missing = await client.get(
                "/api/conversations/00000000-0000-0000-0000-000000000000",
                params={"buyer_id": "A"},
            )
            unauthenticated = await client.get(
                "/api/conversations",
                headers={"Authorization": ""},
            )

            assert forbidden.status_code == 403
            assert forbidden.json() == {"detail": "无权访问其他买家的会话"}
            assert missing.status_code == 404
            assert unauthenticated.status_code == 401

    asyncio.run(run_scenario())


def test_chat_rejects_unknown_conversation_and_invalid_body() -> None:
    async def run_scenario() -> None:
        async with conversation_client() as (client, _service):
            missing = await client.post(
                "/api/chat",
                json={
                    "buyer_id": "A",
                    "conversation_id": "00000000-0000-0000-0000-000000000000",
                    "message": "继续咨询",
                },
            )
            invalid = await client.post(
                "/api/chat",
                json={"buyer_id": "A", "message": "   ", "unknown": True},
            )

            assert missing.status_code == 404
            assert invalid.status_code == 422

    asyncio.run(run_scenario())


def test_chat_maps_unavailable_conversation_to_conflict() -> None:
    async def run_scenario() -> None:
        async with conversation_client() as (client, service):
            created = await client.post(
                "/api/chat",
                json={"buyer_id": "A", "message": "你好"},
            )
            conversation_id = created.json()["conversation_id"]
            conversation = service.get_conversation(
                conversation_id=UUID(conversation_id),
                buyer_id="A",
            )
            service.repository.update(
                conversation.model_copy(update={"mode": ConversationMode.HUMAN})
            )

            response = await client.post(
                "/api/chat",
                json={
                    "buyer_id": "A",
                    "conversation_id": conversation_id,
                    "message": "继续咨询",
                },
            )

            assert response.status_code == 409

    asyncio.run(run_scenario())
