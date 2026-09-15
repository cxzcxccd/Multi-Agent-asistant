"""会话与消息的 FastAPI 路由。"""

from functools import lru_cache
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.modules.conversations.schemas import (
    BuyerId,
    ChatRequest,
    ChatResponse,
    Conversation,
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
    """复用内存仓库，确保不同请求可以访问同一批会话。"""

    return ConversationService()


ConversationServiceDependency = Annotated[
    ConversationService,
    Depends(get_conversation_service),
]
BuyerQuery = Annotated[BuyerId, Query(description="当前买家编号")]


def raise_http_error(error: ConversationServiceError) -> NoReturn:
    """把会话业务异常转换成稳定的 HTTP 错误响应。"""

    if isinstance(error, ConversationNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, ConversationAccessError):
        status_code = status.HTTP_403_FORBIDDEN
    elif isinstance(error, ConversationUnavailableError):
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(error, AssistantReplyError):
        status_code = status.HTTP_502_BAD_GATEWAY
    else:
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

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
) -> ChatResponse:
    """创建新会话或在已有会话中发送一条用户消息。"""

    try:
        return await service.send_message(request)
    except ConversationServiceError as error:
        raise_http_error(error)


@router.get(
    "/conversations",
    response_model=list[Conversation],
    summary="查询买家的会话列表",
)
async def list_conversations(
    buyer_id: BuyerQuery,
    service: ConversationServiceDependency,
) -> list[Conversation]:
    """按最近更新时间倒序返回当前买家的会话。"""

    return service.list_conversations(buyer_id)


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
    buyer_id: BuyerQuery,
    service: ConversationServiceDependency,
) -> Conversation:
    """读取属于当前买家的完整会话和消息历史。"""

    try:
        return service.get_conversation(conversation_id, buyer_id)
    except ConversationServiceError as error:
        raise_http_error(error)
