"""认证接口：注册 / 登录 / 当前用户 / 改资料 / 改密码

密码规则、账号重复、登录失败提示等业务规则都在 app/services/user 里；
这里只做「参数校验 + 调业务 + 包信封」。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.common.response import Response
from app.database import get_db
from app.dependencies.auth import get_current_user
from app.schemas.table import User
from app.services.user import auth_services, user_services
from app.services.user.models import (
    LoginRequest,
    LoginResponse,
    PasswordUpdate,
    RegisterRequest,
    UserResponse,
    UserSelfUpdate,
)

router = APIRouter(prefix="/api/auth", tags=["认证"])

__all__ = ["router"]


@router.post("/register", response_model=Response[UserResponse], summary="学生自助注册")
def register(data: RegisterRequest, db: Session = Depends(get_db)) -> Response[UserResponse]:
    """注册成学生账号（角色/状态由后端固定，不接受前端传参）"""
    user = auth_services.register(db, data)
    return Response.success(message="注册成功", data=user)


@router.post("/login", response_model=Response[LoginResponse], summary="账号密码登录")
def login(data: LoginRequest, db: Session = Depends(get_db)) -> Response[LoginResponse]:
    """返回 JWT 令牌；之后请求带 `Authorization: Bearer <token>`"""
    result = auth_services.login(db, data)
    return Response.success(message="登录成功", data=result)


@router.get("/me", response_model=Response[UserResponse], summary="当前登录用户")
def me(current_user: User = Depends(get_current_user)) -> Response[UserResponse]:
    return Response.success(data=user_services.get_user_info(current_user))


@router.put("/profile", response_model=Response[UserResponse], summary="修改自己的资料")
def update_profile(
    data: UserSelfUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response[UserResponse]:
    """只能改 name / email / phone / avatar（角色和状态在模型层就没有字段）"""
    user = user_services.update_my_info(db, current_user, data)
    return Response.success(message="资料已更新", data=user)


@router.put("/password", response_model=Response[None], summary="修改自己的密码")
def update_password(
    data: PasswordUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response[None]:
    """必须提供原密码；新密码不能与原密码相同"""
    user_services.update_my_password(db, current_user, data)
    return Response.success(message="密码修改成功")
