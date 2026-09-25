"""登录令牌和当前身份的数据格式。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.conversations.schemas import BuyerId


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str
    display_name: str
    role: Literal["buyer", "staff"]
    buyer_id: BuyerId | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    principal: Principal


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=20, max_length=500)


class LogoutRequest(RefreshRequest):
    pass
