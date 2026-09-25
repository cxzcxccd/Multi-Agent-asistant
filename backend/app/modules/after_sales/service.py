"""售后草稿、确认、幂等提交和人工审核规则。"""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.modules.after_sales.repository import (
    ActiveAfterSaleAlreadyExistsError,
    AfterSaleWriteConflictError,
    SqlAfterSaleRepository,
)
from app.modules.after_sales.schemas import (
    AfterSaleRequest,
    AfterSaleStatus,
    CancelAfterSaleDraft,
    CreateAfterSaleDraft,
    ReviewAfterSaleRequest,
    SubmitAfterSaleRequest,
    UpdateAfterSaleDraft,
)
from app.modules.orders.schemas import OrderStatus
from app.modules.orders.service import OrderNotAccessibleError, OrderService


class AfterSaleServiceError(RuntimeError):
    """售后业务异常的基类。"""


class AfterSaleNotFoundError(AfterSaleServiceError):
    """申请不存在或不属于当前买家。"""


class AfterSaleConflictError(AfterSaleServiceError):
    """申请状态、版本或活动申请发生冲突。"""


class AfterSaleIdempotencyConflictError(AfterSaleServiceError):
    """同一幂等键被用于不同的提交内容。"""


class OrderNotEligibleForAfterSaleError(AfterSaleServiceError):
    """订单当前状态不允许创建售后申请。"""


def utc_now() -> datetime:
    return datetime.now(UTC)


class AfterSaleService:
    """执行需要明确状态和版本约束的售后流程。"""

    def __init__(
        self,
        repository: SqlAfterSaleRepository,
        order_service: OrderService,
        id_factory: Callable[[], UUID] = uuid4,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.repository = repository
        self.order_service = order_service
        self._id_factory = id_factory
        self._clock = clock

    def create_draft(self, data: CreateAfterSaleDraft) -> AfterSaleRequest:
        """校验订单归属和状态后创建售后草稿。"""

        try:
            order = self.order_service.get_order(data.order_id, data.buyer_id)
        except OrderNotAccessibleError as error:
            raise AfterSaleNotFoundError(str(error)) from error

        eligible_statuses = {OrderStatus.DELIVERED, OrderStatus.COMPLETED}
        if order.status not in eligible_statuses:
            raise OrderNotEligibleForAfterSaleError(
                "订单当前状态暂不支持创建售后申请"
            )

        created_at = self._clock()
        draft = AfterSaleRequest(
            id=self._id_factory(),
            buyer_id=data.buyer_id,
            order_id=data.order_id,
            request_type=data.request_type,
            reason=data.reason,
            status=AfterSaleStatus.DRAFT,
            version=1,
            created_at=created_at,
            updated_at=created_at,
        )
        try:
            return self.repository.add_draft(draft)
        except ActiveAfterSaleAlreadyExistsError as error:
            raise AfterSaleConflictError(str(error)) from error

    def update_draft(
        self,
        request_id: UUID,
        data: UpdateAfterSaleDraft,
    ) -> AfterSaleRequest:
        """只允许申请所属买家修改仍处于草稿状态的内容。"""

        self._require_buyer_request(request_id, data.buyer_id)
        try:
            return self.repository.update_draft(
                request_id=request_id,
                buyer_id=data.buyer_id,
                request_type=data.request_type,
                reason=data.reason,
                expected_version=data.expected_version,
                changed_at=self._clock(),
            )
        except AfterSaleWriteConflictError as error:
            raise AfterSaleConflictError(str(error)) from error

    def submit(
        self,
        request_id: UUID,
        data: SubmitAfterSaleRequest,
        idempotency_key: str,
    ) -> AfterSaleRequest:
        """以幂等键把买家确认过的草稿提交为待审核。"""

        request_hash = self._submission_hash(request_id, data)
        previous = self.repository.find_by_idempotency_key(
            data.buyer_id,
            idempotency_key,
        )
        if previous is not None:
            return self._resolve_idempotent_result(previous, request_hash)

        self._require_buyer_request(request_id, data.buyer_id)
        try:
            return self.repository.submit(
                request_id=request_id,
                buyer_id=data.buyer_id,
                expected_version=data.expected_version,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                submitted_at=self._clock(),
            )
        except AfterSaleWriteConflictError as error:
            previous = self.repository.find_by_idempotency_key(
                data.buyer_id,
                idempotency_key,
            )
            if previous is not None:
                return self._resolve_idempotent_result(previous, request_hash)
            raise AfterSaleConflictError(str(error)) from error

    def cancel_draft(
        self,
        request_id: UUID,
        data: CancelAfterSaleDraft,
    ) -> AfterSaleRequest:
        """取消未提交草稿，不创建待审核申请。"""

        self._require_buyer_request(request_id, data.buyer_id)
        try:
            return self.repository.cancel_draft(
                request_id=request_id,
                buyer_id=data.buyer_id,
                expected_version=data.expected_version,
                cancelled_at=self._clock(),
            )
        except AfterSaleWriteConflictError as error:
            raise AfterSaleConflictError(str(error)) from error

    def review(
        self,
        request_id: UUID,
        data: ReviewAfterSaleRequest,
    ) -> AfterSaleRequest:
        """人工批准或拒绝待审核申请。"""

        request = self.repository.get(request_id)
        if request is None:
            raise AfterSaleNotFoundError("未找到售后申请")

        decision = AfterSaleStatus(data.decision)
        try:
            return self.repository.review(
                request_id=request_id,
                decision=decision,
                reviewer=data.reviewer,
                reason=data.reason,
                expected_version=data.expected_version,
                reviewed_at=self._clock(),
            )
        except AfterSaleWriteConflictError as error:
            raise AfterSaleConflictError(str(error)) from error

    def list_for_buyer(self, buyer_id: str) -> list[AfterSaleRequest]:
        return self.repository.list_by_buyer(buyer_id)

    def list_pending(self) -> list[AfterSaleRequest]:
        return self.repository.list_pending()

    def get_for_buyer(self, request_id: UUID, buyer_id: str) -> AfterSaleRequest:
        return self._require_buyer_request(request_id, buyer_id)

    def _require_buyer_request(
        self,
        request_id: UUID,
        buyer_id: str,
    ) -> AfterSaleRequest:
        request = self.repository.get_for_buyer(request_id, buyer_id)
        if request is None:
            raise AfterSaleNotFoundError("未找到可查询的售后申请")
        return request

    @staticmethod
    def _submission_hash(request_id: UUID, data: SubmitAfterSaleRequest) -> str:
        payload = {
            "request_id": str(request_id),
            "buyer_id": data.buyer_id,
            "expected_version": data.expected_version,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _resolve_idempotent_result(
        previous: tuple[AfterSaleRequest, str],
        request_hash: str,
    ) -> AfterSaleRequest:
        request, previous_hash = previous
        if previous_hash != request_hash:
            raise AfterSaleIdempotencyConflictError(
                "相同幂等键已经用于不同的申请内容"
            )
        return request
