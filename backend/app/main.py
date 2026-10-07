"""FastAPI 应用入口。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ai.checkpoints import open_sqlite_checkpointer
from app.api.router import api_router
from app.core.config import settings
from app.db.initialize import initialize_database
from app.mcp.server import create_catalog_mcp


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """初始化业务数据库，并在应用生命周期内复用Checkpoint连接。"""

    initialize_database()
    checkpoint_path = Path(settings.checkpoint_database_path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    async with open_sqlite_checkpointer(checkpoint_path.as_posix()) as checkpointer:
        application.state.checkpointer = checkpointer
        application.state.conversation_service = None
        application.state.conversation_service_lock = asyncio.Lock()
        async with application.state.catalog_mcp.session_manager.run():
            yield


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.app_debug,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(api_router, prefix="/api")
    catalog_mcp = create_catalog_mcp()
    application.state.catalog_mcp = catalog_mcp
    application.mount("/mcp", catalog_mcp.streamable_http_app())
    return application


app = create_app()
