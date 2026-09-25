"""售后草稿、幂等提交和人工审核接口测试。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.main import app
from app.modules.auth.schemas import Principal
from app.modules.auth.security import create_access_token
from app.modules.after_sales.repository import SqlAfterSaleRepository
from app.modules.after_sales.router import get_after_sale_service
from app.modules.after_sales.service import AfterSaleService
from app.modules.orders.schemas import Order, OrderItem, OrderStatus
from app.modules.orders.service import OrderService


BUYER_TOKEN = create_access_token(
    Principal(subject="buyer_a", display_name="林同学", role="buyer", buyer_id="A")
)
STAFF_HEADERS = {
    "Authorization": "Bearer "
    + create_access_token(Principal(subject="staff", display_name="客服小周", role="staff"))
}
BUYER_B_HEADERS = {
    "Authorization": "Bearer "
    + create_access_token(
        Principal(subject="buyer_b", display_name="陈同学", role="buyer", buyer_id="B")
    )
}


class FakeOrderRepository:
    """提供售后测试需要的订单归属和状态。"""

    def __init__(self) -> None:
        item = OrderItem(
            product_id="p02",
            product_name="Studio One 头戴耳机",
            quantity=1,
            unit_price=Decimal("499.00"),
        )
        self.orders = {
            "10001": Order(
                id="10001",
                buyer_id="A",
                status=OrderStatus.IN_TRANSIT,
                ordered_at=date(2026, 9, 14),
                items=[item],
                total_amount=Decimal("499.00"),
            ),
            "10002": Order(
                id="10002",
                buyer_id="A",
                status=OrderStatus.DELIVERED,
                ordered_at=date(2026, 9, 10),
                items=[item],
                total_amount=Decimal("499.00"),
            ),
        }

    def list_by_buyer(self, buyer_id: str) -> list[Order]:
        return [order for order in self.orders.values() if order.buyer_id == buyer_id]

    def get_for_buyer(self, order_id: str, buyer_id: str) -> Order | None:
        order = self.orders.get(order_id)
        if order is None or order.buyer_id != buyer_id:
            return None
        return order


@asynccontextmanager
async def after_sales_client(database_path: Path) -> AsyncIterator[AsyncClient]:
    """为一个场景创建隔离数据库和售后服务。"""

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository = SqlAfterSaleRepository(session_factory)
    order_service = OrderService(FakeOrderRepository())
    service = AfterSaleService(repository, order_service)
    app.dependency_overrides[get_after_sale_service] = lambda: service
    transport = ASGITransport(app=app)
    try:
        headers = {"Authorization": f"Bearer {BUYER_TOKEN}"}
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers=headers,
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_after_sale_service, None)
        engine.dispose()


async def create_draft(client: AsyncClient, order_id: str = "10002") -> dict:
    response = await client.post(
        "/api/after-sales/drafts",
        json={
            "buyer_id": "A",
            "order_id": order_id,
            "request_type": "退货",
            "reason": "耳机有一边没有声音",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_create_update_and_cancel_draft(tmp_path: Path) -> None:
    async def run() -> None:
        async with after_sales_client(tmp_path / "draft.db") as client:
            draft = await create_draft(client)
            assert draft["status"] == "draft"
            assert draft["version"] == 1
            assert draft["operations"][0]["action"] == "draft_created"

            updated_response = await client.put(
                f"/api/after-sales/drafts/{draft['id']}",
                json={
                    "buyer_id": "A",
                    "request_type": "换货",
                    "reason": "耳机左侧持续没有声音",
                    "expected_version": 1,
                },
            )
            assert updated_response.status_code == 200
            updated = updated_response.json()
            assert updated["request_type"] == "换货"
            assert updated["version"] == 2

            cancelled_response = await client.post(
                f"/api/after-sales/drafts/{draft['id']}/cancel",
                json={"buyer_id": "A", "expected_version": 2},
            )
            assert cancelled_response.status_code == 200
            cancelled = cancelled_response.json()
            assert cancelled["status"] == "cancelled"
            assert [item["action"] for item in cancelled["operations"]] == [
                "draft_created",
                "draft_updated",
                "cancelled",
            ]

            replacement = await create_draft(client)
            assert replacement["id"] != draft["id"]

    asyncio.run(run())


def test_submit_is_idempotent(tmp_path: Path) -> None:
    async def run() -> None:
        async with after_sales_client(tmp_path / "idempotent.db") as client:
            draft = await create_draft(client)
            path = f"/api/after-sales/drafts/{draft['id']}/submit"
            body = {"buyer_id": "A", "expected_version": 1}
            headers = {"Idempotency-Key": "submit-test-10002"}

            first = await client.post(path, json=body, headers=headers)
            second = await client.post(path, json=body, headers=headers)

            assert first.status_code == 200
            assert second.status_code == 200
            assert first.json() == second.json()
            assert first.json()["status"] == "pending"
            assert first.json()["version"] == 2
            assert [item["action"] for item in first.json()["operations"]] == [
                "draft_created",
                "submitted",
            ]

    asyncio.run(run())


def test_same_idempotency_key_rejects_different_content(tmp_path: Path) -> None:
    async def run() -> None:
        async with after_sales_client(tmp_path / "key-conflict.db") as client:
            draft = await create_draft(client)
            path = f"/api/after-sales/drafts/{draft['id']}/submit"
            headers = {"Idempotency-Key": "same-submit-key"}
            first = await client.post(
                path,
                json={"buyer_id": "A", "expected_version": 1},
                headers=headers,
            )
            conflicting = await client.post(
                path,
                json={"buyer_id": "A", "expected_version": 2},
                headers=headers,
            )

            assert first.status_code == 200
            assert conflicting.status_code == 409
            assert conflicting.json() == {
                "detail": "相同幂等键已经用于不同的申请内容"
            }

    asyncio.run(run())


def test_duplicate_active_request_and_ineligible_order_are_rejected(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        async with after_sales_client(tmp_path / "rules.db") as client:
            await create_draft(client)
            duplicate = await client.post(
                "/api/after-sales/drafts",
                json={
                    "buyer_id": "A",
                    "order_id": "10002",
                    "request_type": "换货",
                    "reason": "同一订单再次创建申请",
                },
            )
            ineligible = await client.post(
                "/api/after-sales/drafts",
                json={
                    "buyer_id": "A",
                    "order_id": "10001",
                    "request_type": "退货",
                    "reason": "运输中的订单申请退货",
                },
            )

            assert duplicate.status_code == 409
            assert ineligible.status_code == 409
            assert ineligible.json()["detail"] == "订单当前状态暂不支持创建售后申请"

    asyncio.run(run())


def test_staff_review_records_result_and_rejects_stale_version(tmp_path: Path) -> None:
    async def run() -> None:
        async with after_sales_client(tmp_path / "review.db") as client:
            draft = await create_draft(client)
            submitted = await client.post(
                f"/api/after-sales/drafts/{draft['id']}/submit",
                json={"buyer_id": "A", "expected_version": 1},
                headers={"Idempotency-Key": "review-submit-key"},
            )
            assert submitted.status_code == 200

            pending = await client.get(
                "/api/staff/after-sales/pending",
                headers=STAFF_HEADERS,
            )
            assert [item["id"] for item in pending.json()] == [draft["id"]]

            review_body = {
                "reviewer": "客服小周",
                "decision": "approved",
                "reason": "故障描述完整，同意申请",
                "expected_version": 2,
            }
            reviewed = await client.post(
                f"/api/staff/after-sales/{draft['id']}/review",
                json=review_body,
                headers=STAFF_HEADERS,
            )
            stale_review = await client.post(
                f"/api/staff/after-sales/{draft['id']}/review",
                json=review_body,
                headers=STAFF_HEADERS,
            )

            assert reviewed.status_code == 200
            assert reviewed.json()["status"] == "approved"
            assert reviewed.json()["reviewer"] == "客服小周"
            assert reviewed.json()["review_reason"] == "故障描述完整，同意申请"
            assert reviewed.json()["version"] == 3
            assert stale_review.status_code == 409
            pending_after_review = await client.get(
                "/api/staff/after-sales/pending",
                headers=STAFF_HEADERS,
            )
            assert pending_after_review.json() == []

    asyncio.run(run())


def test_buyer_cannot_read_or_change_another_buyers_request(tmp_path: Path) -> None:
    async def run() -> None:
        async with after_sales_client(tmp_path / "access.db") as client:
            draft = await create_draft(client)
            detail = await client.get(
                f"/api/after-sales/{draft['id']}",
                params={"buyer_id": "B"},
                headers=BUYER_B_HEADERS,
            )
            update = await client.put(
                f"/api/after-sales/drafts/{draft['id']}",
                json={
                    "buyer_id": "B",
                    "request_type": "退货",
                    "reason": "试图修改其他买家的申请",
                    "expected_version": 1,
                },
                headers=BUYER_B_HEADERS,
            )

            assert detail.status_code == 404
            assert update.status_code == 404

    asyncio.run(run())


def test_old_draft_version_cannot_overwrite_new_content(tmp_path: Path) -> None:
    async def run() -> None:
        async with after_sales_client(tmp_path / "version.db") as client:
            draft = await create_draft(client)
            path = f"/api/after-sales/drafts/{draft['id']}"
            first = await client.put(
                path,
                json={
                    "buyer_id": "A",
                    "request_type": "换货",
                    "reason": "第一次修改后的故障原因",
                    "expected_version": 1,
                },
            )
            stale = await client.put(
                path,
                json={
                    "buyer_id": "A",
                    "request_type": "退货",
                    "reason": "旧页面试图覆盖新的内容",
                    "expected_version": 1,
                },
            )

            assert first.status_code == 200
            assert stale.status_code == 409

    asyncio.run(run())
