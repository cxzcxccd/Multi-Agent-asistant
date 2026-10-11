"""FastAPI 当前身份和角色权限依赖。"""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.modules.auth.schemas import Principal
from app.modules.auth.security import InvalidAccessTokenError, decode_access_token
from app.modules.conversations.schemas import BuyerId

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


def require_buyer(
    user: Annotated[Principal, Depends(get_current_user)],
    acting_buyer_id: Annotated[
        BuyerId | None,
        Header(alias="X-Acting-Buyer-ID"),
    ] = None,
) -> Principal:
    """返回当前买家上下文；管理员必须明确选择代操作的买家。"""

    if user.role == "buyer" and user.buyer_id is not None:
        if acting_buyer_id is not None and acting_buyer_id != user.buyer_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="买家不能切换到其他买家身份",
            )
        return user

    if user.role == "admin":
        if acting_buyer_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="管理员执行买家操作时必须指定买家",
            )
        return user.model_copy(update={"buyer_id": acting_buyer_id})

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要买家或管理员身份")


def require_staff(user: Annotated[Principal, Depends(get_current_user)]) -> Principal:
    if user.role not in {"staff", "admin"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要客服或管理员身份")
    return user


def require_admin(user: Annotated[Principal, Depends(get_current_user)]) -> Principal:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员身份")
    return user


BuyerPrincipal = Annotated[Principal, Depends(require_buyer)]
StaffPrincipal = Annotated[Principal, Depends(require_staff)]
AdminPrincipal = Annotated[Principal, Depends(require_admin)]
SsePrincipal = Annotated[Principal, Depends(get_sse_user)]
