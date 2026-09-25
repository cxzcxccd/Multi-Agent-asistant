"""FastAPI 当前身份和角色权限依赖。"""

from typing import Annotated

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.modules.auth.schemas import Principal
from app.modules.auth.security import InvalidAccessTokenError, decode_access_token

bearer = HTTPBearer(auto_error=False)


def authenticate_token(token: str | None) -> Principal:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    try:
        return decode_access_token(token)
    except InvalidAccessTokenError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    return authenticate_token(credentials.credentials if credentials else None)


def get_sse_user(access_token: Annotated[str | None, Query()] = None) -> Principal:
    return authenticate_token(access_token)


def require_buyer(user: Annotated[Principal, Depends(get_current_user)]) -> Principal:
    if user.role != "buyer" or user.buyer_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要买家身份")
    return user


def require_staff(user: Annotated[Principal, Depends(get_current_user)]) -> Principal:
    if user.role != "staff":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要客服身份")
    return user


BuyerPrincipal = Annotated[Principal, Depends(require_buyer)]
StaffPrincipal = Annotated[Principal, Depends(require_staff)]
SsePrincipal = Annotated[Principal, Depends(get_sse_user)]
