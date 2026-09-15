"""从环境变量读取应用配置。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "极客优选智能客服"
    app_env: str = "development"
    app_debug: bool = False
    model_provider: str = ""
    model_name: str = ""
    model_api_key: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
