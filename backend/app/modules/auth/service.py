"""登录、令牌刷新和退出业务逻辑。"""

from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.modules.auth.models import RefreshTokenRecord, UserRecord
from app.modules.auth.password import verify_password
from app.modules.auth.repository import AuthRepository
from app.modules.auth.schemas import Principal, TokenResponse
from app.modules.auth.security import create_access_token, create_refresh_token, hash_refresh_token


class AuthenticationError(RuntimeError):
    pass


class AuthService:
    def __init__(self, repository: AuthRepository) -> None:
        self.repository = repository

    def login(self, username: str, password: str) -> TokenResponse:
        user = self.repository.get_user_by_username(username)
        if user is None or not user.is_active:
            raise AuthenticationError("用户名或密码错误")
        if not verify_password(user.password_hash, password):
            raise AuthenticationError("用户名或密码错误")
        return self._create_token_pair(user)

    def refresh(self, refresh_token: str) -> TokenResponse:
        token_hash = hash_refresh_token(refresh_token)
        stored_token = self.repository.get_active_refresh_token(token_hash)
        if stored_token is None:
            raise AuthenticationError("刷新令牌无效或已过期")

        user = self.repository.get_user(stored_token.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("账号不可用")

        self.repository.revoke_refresh_token(token_hash)
        return self._create_token_pair(user)

    def logout(self, refresh_token: str) -> None:
        self.repository.revoke_refresh_token(hash_refresh_token(refresh_token))

    def _create_token_pair(self, user: UserRecord) -> TokenResponse:
        principal = Principal(
            subject=user.id,
            display_name=user.display_name,
            role=user.role,
            buyer_id=user.buyer_id,
        )
        refresh_token = create_refresh_token()
        now = datetime.now(UTC)
        record = RefreshTokenRecord(
            user_id=user.id,
            token_hash=hash_refresh_token(refresh_token),
            created_at=now,
            expires_at=now + timedelta(days=settings.refresh_token_days),
        )
        self.repository.save_refresh_token(record)
        return TokenResponse(
            access_token=create_access_token(principal),
            refresh_token=refresh_token,
            expires_in=settings.auth_token_minutes * 60,
            principal=principal,
        )
