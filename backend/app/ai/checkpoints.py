"""LangGraph SQLite Checkpointer的创建和序列化配置。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


ALLOWED_CHECKPOINT_MODULES = [
    ("app", "ai", "query_preprocessor"),
    ("app", "ai", "multi_agent", "schemas"),
]


@asynccontextmanager
async def open_sqlite_checkpointer(
    database_path: str,
) -> AsyncIterator[AsyncSqliteSaver]:
    """打开一个允许恢复本项目自定义状态类型的SQLite Checkpointer。"""

    serializer = JsonPlusSerializer(
        allowed_msgpack_modules=ALLOWED_CHECKPOINT_MODULES
    )
    async with aiosqlite.connect(database_path) as connection:
        checkpointer = AsyncSqliteSaver(connection, serde=serializer)
        await checkpointer.setup()
        yield checkpointer
