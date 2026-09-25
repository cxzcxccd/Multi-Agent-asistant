"""售后申请的事务、并发版本和操作记录数据访问层。"""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.modules.after_sales.models import (
    AfterSaleOperationRecord,
    AfterSaleRecord,
)
from app.modules.after_sales.schemas import (
    AfterSaleOperation,
    AfterSaleRequest,
    AfterSaleStatus,
    AfterSaleType,
)


class AfterSaleRepositoryError(RuntimeError):
    """售后仓库异常的基类。"""


class ActiveAfterSaleAlreadyExistsError(AfterSaleRepositoryError):
    """同一买家订单已经存在草稿或待审核申请。"""


class AfterSaleWriteConflictError(AfterSaleRepositoryError):
    """记录状态或版本已被其他请求修改。"""


class SqlAfterSaleRepository:
    """使用 SQLAlchemy 原子保存售后状态和审计记录。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def add_draft(self, request: AfterSaleRequest) -> AfterSaleRequest:
        """创建草稿，并依靠唯一活动键阻止重复申请。"""

        record = AfterSaleRecord(
            id=str(request.id),
            buyer_id=request.buyer_id,
            order_id=request.order_id,
            request_type=request.request_type.value,
            reason=request.reason,
            status=request.status.value,
            version=request.version,
            active_key=self._active_key(request.buyer_id, request.order_id),
            created_at=request.created_at,
            updated_at=request.updated_at,
        )
        self._append_operation(
            record,
            action="draft_created",
            actor_type="buyer",
            actor_id=request.buyer_id,
            detail="买家创建售后草稿",
            created_at=request.created_at,
        )
        try:
            with self._session_factory.begin() as session:
                session.add(record)
        except IntegrityError as error:
            raise ActiveAfterSaleAlreadyExistsError(
                "该订单已经存在未完成的售后申请"
            ) from error
        saved = self.get_for_buyer(request.id, request.buyer_id)
        if saved is None:
            raise AfterSaleRepositoryError("售后草稿保存后无法读取")
        return saved

    def get_for_buyer(self, request_id: UUID, buyer_id: str) -> AfterSaleRequest | None:
        """按申请编号和买家同时查询。"""

        with self._session_factory() as session:
            statement = self._detail_statement().where(
                AfterSaleRecord.id == str(request_id),
                AfterSaleRecord.buyer_id == buyer_id,
            )
            record = session.scalar(statement)
            return self._to_schema(record) if record is not None else None

    def get(self, request_id: UUID) -> AfterSaleRequest | None:
        """供客服按编号读取申请。"""

        with self._session_factory() as session:
            statement = self._detail_statement().where(
                AfterSaleRecord.id == str(request_id)
            )
            record = session.scalar(statement)
            return self._to_schema(record) if record is not None else None

    def find_by_idempotency_key(
        self,
        buyer_id: str,
        idempotency_key: str,
    ) -> tuple[AfterSaleRequest, str] | None:
        """查找一次已经处理过的提交及其请求摘要。"""

        with self._session_factory() as session:
            statement = self._detail_statement().where(
                AfterSaleRecord.buyer_id == buyer_id,
                AfterSaleRecord.idempotency_key == idempotency_key,
            )
            record = session.scalar(statement)
            if record is None or record.request_hash is None:
                return None
            return self._to_schema(record), record.request_hash

    def list_by_buyer(self, buyer_id: str) -> list[AfterSaleRequest]:
        """返回买家的全部申请，最近更新的排在前面。"""

        with self._session_factory() as session:
            statement = (
                self._detail_statement()
                .where(AfterSaleRecord.buyer_id == buyer_id)
                .order_by(AfterSaleRecord.updated_at.desc())
            )
            records = session.scalars(statement).all()
            return [self._to_schema(record) for record in records]

    def list_pending(self) -> list[AfterSaleRequest]:
        """返回等待人工审核的申请。"""

        with self._session_factory() as session:
            statement = (
                self._detail_statement()
                .where(AfterSaleRecord.status == AfterSaleStatus.PENDING.value)
                .order_by(AfterSaleRecord.submitted_at.asc())
            )
            records = session.scalars(statement).all()
            return [self._to_schema(record) for record in records]

    def update_draft(
        self,
        request_id: UUID,
        buyer_id: str,
        request_type: AfterSaleType,
        reason: str,
        expected_version: int,
        changed_at: datetime,
    ) -> AfterSaleRequest:
        """仅在版本匹配且仍是草稿时更新内容。"""

        values = {
            "request_type": request_type.value,
            "reason": reason,
            "version": expected_version + 1,
            "updated_at": changed_at,
        }
        self._conditional_update(
            request_id=request_id,
            expected_status=AfterSaleStatus.DRAFT,
            expected_version=expected_version,
            values=values,
            operation=("draft_updated", "buyer", buyer_id, "买家修改售后草稿"),
            changed_at=changed_at,
        )
        return self._require_for_buyer(request_id, buyer_id)

    def submit(
        self,
        request_id: UUID,
        buyer_id: str,
        expected_version: int,
        idempotency_key: str,
        request_hash: str,
        submitted_at: datetime,
    ) -> AfterSaleRequest:
        """把草稿原子提交为待审核申请。"""

        values = {
            "status": AfterSaleStatus.PENDING.value,
            "version": expected_version + 1,
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "submitted_at": submitted_at,
            "updated_at": submitted_at,
        }
        try:
            self._conditional_update(
                request_id=request_id,
                expected_status=AfterSaleStatus.DRAFT,
                expected_version=expected_version,
                values=values,
                operation=("submitted", "buyer", buyer_id, "买家确认提交售后申请"),
                changed_at=submitted_at,
            )
        except IntegrityError as error:
            raise AfterSaleWriteConflictError("售后申请提交发生冲突") from error
        return self._require_for_buyer(request_id, buyer_id)

    def cancel_draft(
        self,
        request_id: UUID,
        buyer_id: str,
        expected_version: int,
        cancelled_at: datetime,
    ) -> AfterSaleRequest:
        """取消草稿并释放该订单的活动申请约束。"""

        values = {
            "status": AfterSaleStatus.CANCELLED.value,
            "active_key": None,
            "version": expected_version + 1,
            "updated_at": cancelled_at,
        }
        self._conditional_update(
            request_id=request_id,
            expected_status=AfterSaleStatus.DRAFT,
            expected_version=expected_version,
            values=values,
            operation=("cancelled", "buyer", buyer_id, "买家取消售后草稿"),
            changed_at=cancelled_at,
        )
        return self._require_for_buyer(request_id, buyer_id)

    def review(
        self,
        request_id: UUID,
        decision: AfterSaleStatus,
        reviewer: str,
        reason: str,
        expected_version: int,
        reviewed_at: datetime,
    ) -> AfterSaleRequest:
        """仅在待审核且版本匹配时保存人工审核结果。"""

        values = {
            "status": decision.value,
            "active_key": None,
            "version": expected_version + 1,
            "reviewer": reviewer,
            "review_reason": reason,
            "reviewed_at": reviewed_at,
            "updated_at": reviewed_at,
        }
        self._conditional_update(
            request_id=request_id,
            expected_status=AfterSaleStatus.PENDING,
            expected_version=expected_version,
            values=values,
            operation=("reviewed", "staff", reviewer, reason),
            changed_at=reviewed_at,
        )
        reviewed = self.get(request_id)
        if reviewed is None:
            raise AfterSaleRepositoryError("审核结果保存后无法读取")
        return reviewed

    def _conditional_update(
        self,
        request_id: UUID,
        expected_status: AfterSaleStatus,
        expected_version: int,
        values: dict[str, object],
        operation: tuple[str, str, str, str],
        changed_at: datetime,
    ) -> None:
        """以状态和版本作为条件执行一次原子更新。"""

        with self._session_factory.begin() as session:
            statement = (
                update(AfterSaleRecord)
                .where(
                    AfterSaleRecord.id == str(request_id),
                    AfterSaleRecord.status == expected_status.value,
                    AfterSaleRecord.version == expected_version,
                )
                .values(**values)
            )
            result = session.execute(statement)
            if result.rowcount != 1:
                raise AfterSaleWriteConflictError("售后申请状态或版本已经发生变化")

            action, actor_type, actor_id, detail = operation
            session.add(
                AfterSaleOperationRecord(
                    request_id=str(request_id),
                    action=action,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    detail=detail,
                    created_at=changed_at,
                )
            )

    def _require_for_buyer(self, request_id: UUID, buyer_id: str) -> AfterSaleRequest:
        request = self.get_for_buyer(request_id, buyer_id)
        if request is None:
            raise AfterSaleRepositoryError("售后申请保存后无法读取")
        return request

    @staticmethod
    def _detail_statement():
        return select(AfterSaleRecord).options(
            selectinload(AfterSaleRecord.operations)
        )

    @staticmethod
    def _active_key(buyer_id: str, order_id: str) -> str:
        return f"{buyer_id}:{order_id}"

    @staticmethod
    def _append_operation(
        record: AfterSaleRecord,
        action: str,
        actor_type: str,
        actor_id: str,
        detail: str,
        created_at: datetime,
    ) -> None:
        record.operations.append(
            AfterSaleOperationRecord(
                action=action,
                actor_type=actor_type,
                actor_id=actor_id,
                detail=detail,
                created_at=created_at,
            )
        )

    @classmethod
    def _to_schema(cls, record: AfterSaleRecord) -> AfterSaleRequest:
        operations: list[AfterSaleOperation] = []
        for operation in record.operations:
            operations.append(
                AfterSaleOperation(
                    action=operation.action,
                    actor_type=operation.actor_type,
                    actor_id=operation.actor_id,
                    detail=operation.detail,
                    created_at=cls._with_timezone(operation.created_at),
                )
            )
        return AfterSaleRequest(
            id=UUID(record.id),
            buyer_id=record.buyer_id,
            order_id=record.order_id,
            request_type=record.request_type,
            reason=record.reason,
            status=record.status,
            version=record.version,
            created_at=cls._with_timezone(record.created_at),
            updated_at=cls._with_timezone(record.updated_at),
            submitted_at=cls._optional_timezone(record.submitted_at),
            reviewed_at=cls._optional_timezone(record.reviewed_at),
            reviewer=record.reviewer,
            review_reason=record.review_reason,
            operations=operations,
        )

    @staticmethod
    def _with_timezone(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value

    @classmethod
    def _optional_timezone(cls, value: datetime | None) -> datetime | None:
        return cls._with_timezone(value) if value is not None else None
