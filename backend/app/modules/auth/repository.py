"""用户与刷新令牌数据库访问。"""

from datetime import UTC, datetime
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.auth.models import RefreshTokenRecord, UserRecord


class AuthRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def get_user_by_username(self, username: str) -> UserRecord | None:
        with self.session_factory() as session:
            statement = select(UserRecord).where(UserRecord.username == username)
            return session.scalar(statement)

    def get_user(self, user_id: str) -> UserRecord | None:
        with self.session_factory() as session:
            return session.get(UserRecord, user_id)

    def save_refresh_token(self, record: RefreshTokenRecord) -> None:
        with self.session_factory.begin() as session:
            session.add(record)

    def get_active_refresh_token(self, token_hash: str) -> RefreshTokenRecord | None:
        with self.session_factory() as session:
            statement = select(RefreshTokenRecord).where(
                RefreshTokenRecord.token_hash == token_hash,
                RefreshTokenRecord.revoked_at.is_(None),
                RefreshTokenRecord.expires_at > datetime.now(UTC),
            )
            return session.scalar(statement)

    def revoke_refresh_token(self, token_hash: str) -> bool:
        with self.session_factory.begin() as session:
            statement = select(RefreshTokenRecord).where(
                RefreshTokenRecord.token_hash == token_hash,
                RefreshTokenRecord.revoked_at.is_(None),
            )
            record = session.scalar(statement)
            if record is None:
                return False
            record.revoked_at = datetime.now(UTC)
            return True
