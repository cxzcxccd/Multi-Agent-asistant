"""会话与消息的 FastAPI 路由。"""

import asyncio
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.stream import encode_sse
from app.modules.auth.dependencies import BuyerPrincipal, SsePrincipal, StaffPrincipal
from app.db.initialize import initialize_database
from app.db.session import get_session_factory
from app.modules.conversations.repository import SqlConversationRepository
from app.modules.conversations.schemas import (
    BuyerId,
    ChatRequest,
    ChatResponse,
    ChatStreamError,
    Conversation,
    HandoffRequest,
    StaffModeRequest,
    StaffReplyRequest,
)
from app.modules.conversations.service import (
    AssistantReplyError,
    ConversationAccessError,
    ConversationNotFoundError,
    ConversationService,
    ConversationServiceError,
    ConversationUnavailableError,
)

router = APIRouter(tags=["conversations"])


@lru_cache(maxsize=1)
def get_conversation_service() -> ConversationService:
    """复用数据库会话仓库，确保重启后仍能恢复会话。"""

    initialize_database()
    repository = SqlConversationRepository(get_session_factory())
    return ConversationService(repository=repository)


ConversationServiceDependency = Annotated[
    ConversationService,
    Depends(get_conversation_service),
]
BuyerQuery = Annotated[BuyerId, Query(description="当前买家编号")]


def conversation_error_status(error: ConversationServiceError) -> int:
    """返回会话业务异常对应的 HTTP 状态码。"""

    if isinstance(error, ConversationNotFoundError):
        return status.HTTP_404_NOT_FOUND
    if isinstance(error, ConversationAccessError):
        return status.HTTP_403_FORBIDDEN
    if isinstance(error, ConversationUnavailableError):
        return status.HTTP_409_CONFLICT
    if isinstance(error, AssistantReplyError):
        return status.HTTP_502_BAD_GATEWAY
    return status.HTTP_500_INTERNAL_SERVER_ERROR


def raise_http_error(error: ConversationServiceError) -> NoReturn:
    """把会话业务异常转换成稳定的 HTTP 错误响应。"""

    status_code = conversation_error_status(error)
    raise HTTPException(status_code=status_code, detail=str(error)) from error


@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={
        status.HTTP_403_FORBIDDEN: {"description": "无权访问该会话"},
        status.HTTP_404_NOT_FOUND: {"description": "会话不存在"},
        status.HTTP_409_CONFLICT: {"description": "会话当前不能由 AI 回复"},
        status.HTTP_502_BAD_GATEWAY: {"description": "AI 客服调用失败"},
    },
    summary="发送消息给 AI 客服",
)
async def chat(
    request: ChatRequest,
    service: ConversationServiceDependency,
    user: BuyerPrincipal,
) -> ChatResponse:
    """创建新会话或在已有会话中发送一条用户消息。"""

    try:
        authenticated_request = request.model_copy(update={"buyer_id": user.buyer_id})
        return await service.send_message(authenticated_request)
    except ConversationServiceError as error:
        raise_http_error(error)


@router.post(
    "/chat/stream",
    response_class=StreamingResponse,
    summary="以 SSE 方式发送消息给 AI 客服",
)
async def stream_chat(
    request: ChatRequest,
    service: ConversationServiceDependency,
    user: BuyerPrincipal,
) -> StreamingResponse:
    """逐步返回模型文本、工具进度和最终会话结果。"""

    async def event_source() -> AsyncIterator[str]:
        try:
            authenticated_request = request.model_copy(update={"buyer_id": user.buyer_id})
            async for event in service.stream_message(authenticated_request):
                yield encode_sse(event.name, event.data)
        except ConversationServiceError as error:
            error_data = ChatStreamError(
                status=conversation_error_status(error),
                detail=str(error),
            )
            yield encode_sse("error", error_data)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/conversations",
    response_model=list[Conversation],
    summary="查询买家的会话列表",
)
async def list_conversations(
    service: ConversationServiceDependency,
    user: BuyerPrincipal,
) -> list[Conversation]:
    """按最近更新时间倒序返回当前买家的会话。"""

    return service.list_conversations(user.buyer_id)


@router.get(
    "/conversation-events",
    response_class=StreamingResponse,
    summary="订阅会话和消息变化",
)
async def stream_conversation_events(
    request: Request,
    buyer_id: BuyerQuery,
    service: ConversationServiceDependency,
    user: SsePrincipal,
) -> StreamingResponse:
    """只在会话快照发生变化时推送，并定期发送心跳。"""

    if user.role != "buyer" or user.buyer_id != buyer_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权订阅该买家的会话")

    async def event_source() -> AsyncIterator[str]:
        previous_version = ""
        heartbeat_ticks = 0
        while not await request.is_disconnected():
            conversations = service.list_conversations(buyer_id)
            version_parts: list[str] = []
            for conversation in conversations:
                version_parts.append(
                    f"{conversation.id}:{conversation.updated_at.isoformat()}:{conversation.mode.value}"
                )
            current_version = "|".join(version_parts)
            if current_version != previous_version:
                payload = [item.model_dump(mode="json") for item in conversations]
                yield encode_sse("snapshot", payload)
                previous_version = current_version
                heartbeat_ticks = 0
            elif heartbeat_ticks >= 15:
                yield ": heartbeat\n\n"
                heartbeat_ticks = 0

            heartbeat_ticks += 1
            await asyncio.sleep(1)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/staff/conversation-events",
    response_class=StreamingResponse,
    summary="客服订阅全部会话变化",
)
async def stream_staff_conversation_events(
    request: Request,
    service: ConversationServiceDependency,
    user: SsePrincipal,
) -> StreamingResponse:
    """使用客服令牌推送工作台所需的全部会话快照。"""

    if user.role != "staff":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要客服身份")

    async def event_source() -> AsyncIterator[str]:
        previous_version = ""
        heartbeat_ticks = 0
        while not await request.is_disconnected():
            conversations = service.list_all_conversations()
            version_parts: list[str] = []
            for conversation in conversations:
                version_parts.append(
                    f"{conversation.id}:{conversation.updated_at.isoformat()}:{conversation.mode.value}"
                )
            current_version = "|".join(version_parts)
            if current_version != previous_version:
                payload: list[dict[str, object]] = []
                for conversation in conversations:
                    payload.append(conversation.model_dump(mode="json"))
                yield encode_sse("snapshot", payload)
                previous_version = current_version
                heartbeat_ticks = 0
            elif heartbeat_ticks >= 15:
                yield ": heartbeat\n\n"
                heartbeat_ticks = 0

            heartbeat_ticks += 1
            await asyncio.sleep(1)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=Conversation,
    responses={
        status.HTTP_403_FORBIDDEN: {"description": "无权访问该会话"},
        status.HTTP_404_NOT_FOUND: {"description": "会话不存在"},
    },
    summary="查询会话详情",
)
async def get_conversation(
    conversation_id: Annotated[UUID, Path(description="会话编号")],
    service: ConversationServiceDependency,
    user: BuyerPrincipal,
) -> Conversation:
    """读取属于当前买家的完整会话和消息历史。"""

    try:
        return service.get_conversation(conversation_id, user.buyer_id)
    except ConversationServiceError as error:
        raise_http_error(error)


@router.post(
    "/handoff",
    response_model=Conversation,
    summary="申请人工客服接管",
)
async def request_handoff(
    request: HandoffRequest,
    service: ConversationServiceDependency,
    user: BuyerPrincipal,
) -> Conversation:
    """创建等待队列会话，或把已有 AI 会话转入等待队列。"""

    try:
        authenticated_request = request.model_copy(update={"buyer_id": user.buyer_id})
        return service.request_handoff(authenticated_request)
    except ConversationServiceError as error:
        raise_http_error(error)


@router.post(
    "/staff/conversations/{conversation_id}/mode",
    response_model=Conversation,
    summary="修改人工服务状态",
)
async def change_service_mode(
    conversation_id: Annotated[UUID, Path(description="会话编号")],
    request: StaffModeRequest,
    service: ConversationServiceDependency,
    user: StaffPrincipal,
) -> Conversation:
    """客服接管会话、恢复 AI 服务或结束会话。"""

    try:
        authenticated_request = request.model_copy(update={"staff_id": user.display_name})
        return service.change_service_mode(conversation_id, authenticated_request)
    except ConversationServiceError as error:
        raise_http_error(error)


@router.post(
    "/staff/conversations/{conversation_id}/messages",
    response_model=Conversation,
    summary="发送人工客服回复",
)
async def add_staff_reply(
    conversation_id: Annotated[UUID, Path(description="会话编号")],
    request: StaffReplyRequest,
    service: ConversationServiceDependency,
    user: StaffPrincipal,
) -> Conversation:
    """只允许在人工服务状态下保存客服消息。"""

    try:
        authenticated_request = request.model_copy(update={"staff_id": user.display_name})
        return service.add_staff_reply(conversation_id, authenticated_request)
    except ConversationServiceError as error:
        raise_http_error(error)
