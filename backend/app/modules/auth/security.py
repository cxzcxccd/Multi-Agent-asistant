"""使用 HMAC 签发和校验短期访问令牌。"""

import base64
import hashlib
import hmac
import json
import time
import secrets
from typing import Any

from app.core.config import settings
from app.modules.auth.schemas import Principal


class InvalidAccessTokenError(RuntimeError):
    pass


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def create_access_token(principal: Principal) -> str:
    now = int(time.time())
    payload = {
        **principal.model_dump(mode="json"),
        "iat": now,
        "exp": now + settings.auth_token_minutes * 60,
    }
    encoded_payload = _encode(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(
        settings.auth_secret.get_secret_value().encode(),
        encoded_payload.encode(),
        hashlib.sha256,
    ).digest()
    return f"{encoded_payload}.{_encode(signature)}"


def create_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def decode_access_token(token: str) -> Principal:
    try:
        encoded_payload, encoded_signature = token.split(".", maxsplit=1)
        expected = hmac.new(
            settings.auth_secret.get_secret_value().encode(),
            encoded_payload.encode(),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(expected, _decode(encoded_signature)):
            raise InvalidAccessTokenError("访问令牌签名无效")
        payload: dict[str, Any] = json.loads(_decode(encoded_payload))
        if int(payload["exp"]) <= int(time.time()):
            raise InvalidAccessTokenError("访问令牌已过期")
        return Principal(
            subject=payload["subject"],
            display_name=payload["display_name"],
            role=payload["role"],
            buyer_id=payload.get("buyer_id"),
        )
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise InvalidAccessTokenError("访问令牌格式无效") from error
