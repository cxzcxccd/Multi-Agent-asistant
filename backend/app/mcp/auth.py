"""订单MCP内部凭证及请求级买家身份。"""

import hashlib
import time
from contextvars import ContextVar
from uuid import uuid4

import jwt
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings

current_mcp_buyer: ContextVar[str | None] = ContextVar("mcp_buyer", default=None)
TOKEN_AUDIENCE = "commerce-orders-mcp"
TOKEN_ISSUER = "commerce-agent-backend"


def signing_key() -> bytes:
    """从服务端密钥派生专用签名密钥，与浏览器访问令牌隔离。"""

    secret = settings.auth_secret.get_secret_value().encode()
    return hashlib.sha256(b"orders-mcp-internal:" + secret).digest()


def create_order_token(buyer_id: str) -> str:
    """只接受服务端已验证的买家上下文，签发一分钟内部凭证。"""

    if buyer_id not in {"A", "B"}:
        raise ValueError("订单MCP缺少可信买家身份")
    now = int(time.time())
    return jwt.encode(
        {
            "sub": buyer_id,
            "iss": TOKEN_ISSUER,
            "aud": TOKEN_AUDIENCE,
            "scope": "orders:read",
            "iat": now,
            "exp": now + 60,
            "jti": str(uuid4()),
        },
        signing_key(),
        algorithm="HS256",
    )


def verify_order_token(token: str) -> str:
    """校验签名、有效期、用途及读取权限。"""

    payload = jwt.decode(
        token,
        signing_key(),
        algorithms=["HS256"],
        audience=TOKEN_AUDIENCE,
        issuer=TOKEN_ISSUER,
        options={"require": ["sub", "exp", "iat", "aud", "iss", "scope", "jti"]},
    )
    buyer_id = payload["sub"]
    if buyer_id not in {"A", "B"} or payload["scope"] != "orders:read":
        raise jwt.InvalidTokenError("订单MCP凭证权限无效")
    return buyer_id


class OrderMcpAuthentication:
    """保护整个订单MCP端点，并避免并发请求的买家身份串用。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        try:
            if not authorization.startswith("Bearer "):
                raise jwt.InvalidTokenError("缺少凭证")
            buyer_id = verify_order_token(authorization[7:])
        except jwt.InvalidTokenError:
            response = JSONResponse(
                {"detail": "订单MCP身份凭证无效或已过期"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        context_token = current_mcp_buyer.set(buyer_id)
        try:
            await self.app(scope, receive, send)
        finally:
            current_mcp_buyer.reset(context_token)
