"""升级数据库结构，导入演示数据并同步 Milvus 知识索引。"""

from pathlib import Path
from threading import Lock
from datetime import UTC, datetime
import logging

from alembic import command
from alembic.config import Config
from pydantic import TypeAdapter
from sqlalchemy import func, select

from app.core.config import settings
from app.db.session import get_session_factory
from app.modules.auth.models import UserRecord
from app.modules.auth.password import hash_password
from app.modules.catalog.models import ProductRecord
from app.modules.catalog.repository import ProductRepository
from app.modules.knowledge.loader import load_knowledge_directory
from app.modules.knowledge.embeddings import create_embedding_provider
from app.modules.knowledge.indexer import KnowledgeIndexer
from app.modules.knowledge.repository import KnowledgeRepository
from app.modules.knowledge.vector_store import (
    VectorStoreUnavailableError,
    create_knowledge_vector_store,
)
from app.modules.orders.models import (
    LogisticsEventRecord,
    OrderItemRecord,
    OrderRecord,
)
from app.modules.orders.schemas import OrderSeed

BACKEND_ROOT = Path(__file__).resolve().parents[2]
_initialization_lock = Lock()
_initialized_database_urls: set[str] = set()
logger = logging.getLogger(__name__)


def initialize_database() -> None:
    """升级关系数据库，导入演示数据并尝试同步 Milvus 索引。"""

    database_url = settings.database_url
    if database_url in _initialized_database_urls:
        return

    with _initialization_lock:
        if database_url in _initialized_database_urls:
            return

        _upgrade_database(database_url)
        _seed_products()
        _seed_orders()
        _seed_users()
        _seed_knowledge()
        _initialized_database_urls.add(database_url)


def _upgrade_database(database_url: str) -> None:
    """执行项目内全部 Alembic 迁移。"""

    config_path = BACKEND_ROOT / "alembic.ini"
    migrations_path = BACKEND_ROOT / "alembic"
    config = Config(str(config_path))
    config.set_main_option("script_location", str(migrations_path))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def _seed_products() -> None:
    """商品表为空时导入版本库中的固定演示商品。"""

    session_factory = get_session_factory()
    with session_factory.begin() as session:
        product_count = session.scalar(select(func.count()).select_from(ProductRecord))
        if product_count:
            return

        products = ProductRepository().list_products()
        for product in products:
            record = ProductRecord(**product.model_dump(mode="python"))
            session.add(record)


def _seed_orders() -> None:
    """订单表为空时导入固定的归属和物流测试场景。"""

    session_factory = get_session_factory()
    with session_factory.begin() as session:
        order_count = session.scalar(select(func.count()).select_from(OrderRecord))
        if order_count:
            return

        seed_path = BACKEND_ROOT / "data" / "seed" / "orders.json"
        raw_data = seed_path.read_text(encoding="utf-8")
        orders = TypeAdapter(list[OrderSeed]).validate_json(raw_data)

        for order in orders:
            record = OrderRecord(
                id=order.id,
                buyer_id=order.buyer_id,
                status=order.status.value,
                ordered_at=order.ordered_at,
                carrier=order.carrier,
                tracking_number=order.tracking_number,
            )
            for item in order.items:
                record.items.append(
                    OrderItemRecord(
                        product_id=item.product_id,
                        product_name=item.product_name,
                        quantity=item.quantity,
                        unit_price=item.unit_price,
                    )
                )
            for position, event in enumerate(order.logistics):
                record.logistics_events.append(
                    LogisticsEventRecord(
                        position=position,
                        occurred_at=event.occurred_at,
                        title=event.title,
                        detail=event.detail,
                    )
                )
            session.add(record)


def _seed_users() -> None:
    """数据库没有账号时创建本地演示用户。"""

    session_factory = get_session_factory()
    with session_factory.begin() as session:
        user_count = session.scalar(select(func.count()).select_from(UserRecord))
        if user_count:
            return

        now = datetime.now(UTC)
        demo_users = [
            UserRecord(
                username="buyer_a",
                display_name="林同学",
                password_hash=hash_password(settings.demo_buyer_a_password.get_secret_value()),
                role="buyer",
                buyer_id="A",
                is_active=True,
                created_at=now,
            ),
            UserRecord(
                username="buyer_b",
                display_name="陈同学",
                password_hash=hash_password(settings.demo_buyer_b_password.get_secret_value()),
                role="buyer",
                buyer_id="B",
                is_active=True,
                created_at=now,
            ),
            UserRecord(
                username="staff",
                display_name="客服小周",
                password_hash=hash_password(settings.demo_staff_password.get_secret_value()),
                role="staff",
                buyer_id=None,
                is_active=True,
                created_at=now,
            ),
        ]
        session.add_all(demo_users)


def _seed_knowledge() -> None:
    """同步知识正文到 SQLite，并把向量索引写入 Milvus。"""

    repository = KnowledgeRepository(get_session_factory())
    knowledge_path = BACKEND_ROOT / "data" / "knowledge"
    chunks = load_knowledge_directory(knowledge_path)
    provider = create_embedding_provider()
    vector_store = create_knowledge_vector_store()
    try:
        KnowledgeIndexer(repository, provider, vector_store).rebuild(chunks)
    except VectorStoreUnavailableError as error:
        logger.warning("Milvus 暂不可用，知识向量将在服务恢复后重建：%s", error)
