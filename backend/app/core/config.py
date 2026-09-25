"""从环境变量读取应用配置。"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[2] / "data" / "app.db"
DEFAULT_INTENT_CACHE_PATH = (
    Path(__file__).resolve().parents[3] / ".local" / "fastembed"
)


class Settings(BaseSettings):
    """应用运行配置。

    模型配置在真正创建模型客户端时才进行完整性检查，因此没有密钥时，
    商品接口和健康检查仍然可以正常启动。
    """

    app_name: str = "极客优选智能客服"
    app_env: str = "development"
    app_debug: bool = False
    cors_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]
    database_url: str = f"sqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"
    auth_secret: SecretStr = SecretStr("local-demo-secret-change-before-deploy")
    auth_token_minutes: int = Field(default=30, ge=5, le=1440)
    refresh_token_days: int = Field(default=7, ge=1, le=90)
    demo_buyer_a_password: SecretStr = SecretStr("buyer-a-demo")
    demo_buyer_b_password: SecretStr = SecretStr("buyer-b-demo")
    demo_staff_password: SecretStr = SecretStr("staff-demo")
    model_provider: str = "openai"
    model_name: str = ""
    model_api_key: SecretStr | None = None
    model_base_url: str = ""
    model_temperature: float = Field(default=0.1, ge=0, le=2)
    model_timeout_seconds: float = Field(default=30, gt=0, le=300)
    model_max_retries: int = Field(default=2, ge=0, le=5)
    embedding_provider: str = "fastembed"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_dimensions: int = Field(default=512, ge=64, le=4096)
    embedding_cache_dir: str = str(DEFAULT_INTENT_CACHE_PATH)
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_token: SecretStr | None = None
    milvus_database: str = "default"
    milvus_collection: str = "knowledge_chunks"
    milvus_timeout_seconds: float = Field(default=10, gt=0, le=120)
    intent_embedding_provider: str = "fastembed"
    intent_embedding_model: str = "BAAI/bge-small-zh-v1.5"
    intent_embedding_cache_dir: str = str(DEFAULT_INTENT_CACHE_PATH)
    intent_route_min_score: float = Field(default=0.55, ge=-1, le=1)
    intent_route_min_margin: float = Field(default=0.0, ge=0, le=1)
    knowledge_vector_weight: float = Field(default=0.7, ge=0, le=1)
    knowledge_min_score: float = Field(default=0.32, ge=0, le=1)
    knowledge_chunk_size: int = Field(default=500, ge=100, le=2000)
    knowledge_chunk_overlap: int = Field(default=60, ge=0, le=500)
    knowledge_vector_candidates: int = Field(default=20, ge=5, le=200)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """读取并缓存应用配置。"""

    return Settings()


settings = get_settings()
