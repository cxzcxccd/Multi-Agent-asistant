"""数据库引擎和会话工厂。"""

from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def create_database_engine(database_url: str) -> Engine:
    """创建数据库引擎，并为 SQLite 开启外键约束。"""

    connect_args: dict[str, object] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    engine = create_engine(database_url, connect_args=connect_args)

    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def enable_sqlite_foreign_keys(dbapi_connection: Any, _record: object) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """复用应用数据库引擎。"""

    return create_database_engine(settings.database_url)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """创建每次调用独立开启 Session 的工厂。"""

    return sessionmaker(bind=get_engine(), expire_on_commit=False)
