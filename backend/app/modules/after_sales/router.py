"""售后草稿、提交、查询和人工审核接口。"""

from functools import lru_cache
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Path, status

from app.db.initialize import initialize_database
from app.db.session import get_session_factory
from app.modules.auth.dependencies import BuyerPrincipal, StaffPrincipal
from app.modules.after_sales.repository import SqlAfterSaleRepository
from app.modules.after_sales.schemas import (
    AfterSaleRequest,
    CancelAfterSaleDraft,
    CreateAfterSaleDraft,
    ReviewAfterSaleRequest,
    SubmitAfterSaleRequest,
    UpdateAfterSaleDraft,
)
from app.modules.after_sales.service import (
    AfterSaleConflictError,
    AfterSaleIdempotencyConflictError,
    AfterSaleNotFoundError,
    AfterSaleService,
    AfterSaleServiceError,
    OrderNotEligibleForAfterSaleError,
)
from app.modules.orders.repository import SqlOrderRepository
from app.modules.orders.service import OrderService

router = APIRouter(tags=["after-sales"])


@lru_cache(maxsize=1)
def get_after_sale_service() -> AfterSaleService:
    """复用售后、订单数据库仓库和业务服务。"""

    initialize_database()
    session_factory = get_session_factory()
    repository = SqlAfterSaleRepository(session_factory)
    order_service = OrderService(SqlOrderRepository(session_factory))
    return AfterSaleService(repository, order_service)


AfterSaleServiceDependency = Annotated[
    AfterSaleService,
    Depends(get_after_sale_service),
]
RequestIdPath = Annotated[UUID, Path(description="售后申请编号")]
IdempotencyKeyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=100),
]


def raise_after_sale_error(error: AfterSaleServiceError) -> NoReturn:
    """把售后业务异常转换为稳定的 HTTP 状态码。"""

    if isinstance(error, AfterSaleNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(
        error,
        (
            AfterSaleConflictError,
            AfterSaleIdempotencyConflictError,
            OrderNotEligibleForAfterSaleError,
        ),
    ):
        status_code = status.HTTP_409_CONFLICT
    else:
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    raise HTTPException(status_code=status_code, detail=str(error)) from error


@router.post(
    "/after-sales/drafts",
    response_model=AfterSaleRequest,
    status_code=status.HTTP_201_CREATED,
    summary="创建售后草稿",
)
async def create_draft(
    data: CreateAfterSaleDraft,
    service: AfterSaleServiceDependency,
    user: BuyerPrincipal,
) -> AfterSaleRequest:
    """校验订单后创建草稿，尚不进入人工审核。"""

    try:
        authenticated_data = data.model_copy(update={"buyer_id": user.buyer_id})
        return service.create_draft(authenticated_data)
    except AfterSaleServiceError as error:
        raise_after_sale_error(error)


@router.put(
    "/after-sales/drafts/{request_id}",
    response_model=AfterSaleRequest,
    summary="修改售后草稿",
)
async def update_draft(
    request_id: RequestIdPath,
    data: UpdateAfterSaleDraft,
    service: AfterSaleServiceDependency,
    user: BuyerPrincipal,
) -> AfterSaleRequest:
    """使用版本号修改仍未提交的草稿。"""

    try:
        authenticated_data = data.model_copy(update={"buyer_id": user.buyer_id})
        return service.update_draft(request_id, authenticated_data)
    except AfterSaleServiceError as error:
        raise_after_sale_error(error)


@router.post(
    "/after-sales/drafts/{request_id}/submit",
    response_model=AfterSaleRequest,
    summary="确认提交售后申请",
)
async def submit_draft(
    request_id: RequestIdPath,
    data: SubmitAfterSaleRequest,
    idempotency_key: IdempotencyKeyHeader,
    service: AfterSaleServiceDependency,
    user: BuyerPrincipal,
) -> AfterSaleRequest:
    """明确确认后幂等提交，同一个键重复请求返回同一结果。"""

    try:
        authenticated_data = data.model_copy(update={"buyer_id": user.buyer_id})
        return service.submit(request_id, authenticated_data, idempotency_key)
    except AfterSaleServiceError as error:
        raise_after_sale_error(error)


@router.post(
    "/after-sales/drafts/{request_id}/cancel",
    response_model=AfterSaleRequest,
    summary="取消售后草稿",
)
async def cancel_draft(
    request_id: RequestIdPath,
    data: CancelAfterSaleDraft,
    service: AfterSaleServiceDependency,
    user: BuyerPrincipal,
) -> AfterSaleRequest:
    """取消草稿并释放该订单，取消不会产生待审核申请。"""

    try:
        authenticated_data = data.model_copy(update={"buyer_id": user.buyer_id})
        return service.cancel_draft(request_id, authenticated_data)
    except AfterSaleServiceError as error:
        raise_after_sale_error(error)


@router.get(
    "/after-sales",
    response_model=list[AfterSaleRequest],
    summary="查询买家的售后申请",
)
async def list_after_sales(
    service: AfterSaleServiceDependency,
    user: BuyerPrincipal,
) -> list[AfterSaleRequest]:
    return service.list_for_buyer(user.buyer_id)


@router.get(
    "/after-sales/{request_id}",
    response_model=AfterSaleRequest,
    summary="查询售后申请详情",
)
async def get_after_sale(
    request_id: RequestIdPath,
    service: AfterSaleServiceDependency,
    user: BuyerPrincipal,
) -> AfterSaleRequest:
    try:
        return service.get_for_buyer(request_id, user.buyer_id)
    except AfterSaleServiceError as error:
        raise_after_sale_error(error)


@router.get(
    "/staff/after-sales/pending",
    response_model=list[AfterSaleRequest],
    summary="查询待审核售后申请",
)
async def list_pending_after_sales(
    service: AfterSaleServiceDependency,
    user: StaffPrincipal,
) -> list[AfterSaleRequest]:
    """返回当前客服有权处理的待审核队列。"""

    return service.list_pending()


@router.post(
    "/staff/after-sales/{request_id}/review",
    response_model=AfterSaleRequest,
    summary="审核售后申请",
)
async def review_after_sale(
    request_id: RequestIdPath,
    data: ReviewAfterSaleRequest,
    service: AfterSaleServiceDependency,
    user: StaffPrincipal,
) -> AfterSaleRequest:
    """记录客服、理由和审核结果；批准不表示退款到账。"""

    try:
        authenticated_data = data.model_copy(update={"reviewer": user.display_name})
        return service.review(request_id, authenticated_data)
    except AfterSaleServiceError as error:
        raise_after_sale_error(error)
