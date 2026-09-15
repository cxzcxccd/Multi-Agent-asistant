"""从环境变量读取应用配置。"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用运行配置。

    模型配置在真正创建模型客户端时才进行完整性检查，因此没有密钥时，
    商品接口和健康检查仍然可以正常启动。
    """

    app_name: str = "极客优选智能客服"
    app_env: str = "development"
    app_debug: bool = False
    cors_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]
    model_provider: str = "openai"
    model_name: str = ""
    model_api_key: SecretStr | None = None
    model_base_url: str = ""
    model_temperature: float = Field(default=0.1, ge=0, le=2)
    model_timeout_seconds: float = Field(default=30, gt=0, le=300)
    model_max_retries: int = Field(default=2, ge=0, le=5)

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
