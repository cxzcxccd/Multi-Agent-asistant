"""数据库账号登录、刷新、退出和当前身份接口。"""

from functools import lru_cache
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, status

from app.db.initialize import initialize_database
from app.db.session import get_session_factory
from app.modules.auth.dependencies import BuyerPrincipal, StaffPrincipal, get_current_user
from app.modules.auth.repository import AuthRepository
from app.modules.auth.schemas import LoginRequest, LogoutRequest, Principal, RefreshRequest, TokenResponse
from app.modules.auth.service import AuthenticationError, AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@lru_cache(maxsize=1)
def get_auth_service() -> AuthService:
    initialize_database()
    return AuthService(AuthRepository(get_session_factory()))


AuthServiceDependency = Annotated[AuthService, Depends(get_auth_service)]
CurrentPrincipal = Annotated[Principal, Depends(get_current_user)]


def raise_authentication_error(error: AuthenticationError) -> NoReturn:
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, service: AuthServiceDependency) -> TokenResponse:
    try:
        return service.login(data.username, data.password)
    except AuthenticationError as error:
        raise_authentication_error(error)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(data: RefreshRequest, service: AuthServiceDependency) -> TokenResponse:
    try:
        return service.refresh(data.refresh_token)
    except AuthenticationError as error:
        raise_authentication_error(error)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(data: LogoutRequest, service: AuthServiceDependency) -> None:
    service.logout(data.refresh_token)


@router.get("/me", response_model=Principal)
async def current_user(user: CurrentPrincipal) -> Principal:
    return user


@router.get("/buyer", response_model=Principal)
async def current_buyer(user: BuyerPrincipal) -> Principal:
    return user


@router.get("/staff", response_model=Principal)
async def current_staff(user: StaffPrincipal) -> Principal:
    return user
