"""用户管理接口（管理员）

`app/services/user` 里已经有完整的用户增删改查与「不能禁用自己 / 不能删除有预约的用户」
等规则，但接口层一直只有 `/api/auth`（用户操作自己）。管理员要给学生改角色、
禁用账号、重置密码，前端就必须有这组路由。

和 `/api/auth` 的分工：

    /api/auth/*     当前登录用户操作自己（改资料 / 改密码，角色和状态改不了）
    /api/users/*    管理员操作别人（此处全部 require_admin，非管理员 403）

处理人身份从登录态取（`operator`），不接受前端传 operator_id —— 否则可以伪造操作人。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.common.response import Response
from app.database import get_db
from app.dependencies.auth import require_admin
from app.schemas.models import PageResponse
from app.schemas.table import User
from app.services.user import user_services
from app.services.user.models import (
    PasswordReset,
    UserCreate,
    UserResponse,
    UserUpdate,
)

router = APIRouter(prefix="/api/users", tags=["管理员·用户"])

__all__ = ["router"]

_DEFAULT_PAGE_SIZE = 10
_MAX_PAGE_SIZE = 100


@router.get(
    "",
    response_model=Response[PageResponse[UserResponse]],
    summary="用户列表（管理员）",
)
def list_users(
    keyword: str | None = Query(None, max_length=50, description="匹配账号、姓名、邮箱、手机号"),
    role: list[str] | None = Query(None, description="角色，可重复传：student / admin"),
    status: int | None = Query(None, ge=0, le=1, description="状态: 0-禁用, 1-正常"),
    page: int = Query(1, ge=1),
    page_size: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[PageResponse[UserResponse]]:
    result = user_services.get_user_page(
        db,
        page=page,
        page_size=page_size,
        keyword=keyword,
        roles=role,
        status=status,
    )
    return Response.success(data=result)


@router.get(
    "/{user_id}",
    response_model=Response[UserResponse],
    summary="用户详情（管理员）",
)
def get_user(
    user_id: int,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[UserResponse]:
    return Response.success(data=user_services.get_user_detail(db, user_id))


@router.post(
    "",
    response_model=Response[UserResponse],
    summary="新增用户（管理员）",
)
def create_user(
    data: UserCreate,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[UserResponse]:
    """管理员建号可以指定角色与状态；自助注册（/api/auth/register）只能建学生。"""
    return Response.success(message="用户已创建", data=user_services.create_user(db, data))


@router.put(
    "/{user_id}",
    response_model=Response[UserResponse],
    summary="更新用户（管理员）",
)
def update_user(
    user_id: int,
    data: UserUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[UserResponse]:
    """只会改传了值的字段；管理员不能把自己降级或禁用（service 层拦截）。"""
    return Response.success(
        message="用户已更新",
        data=user_services.admin_update_user(db, user_id, data, operator=admin),
    )


@router.put(
    "/{user_id}/password",
    response_model=Response[None],
    summary="重置用户密码（管理员）",
)
def reset_password(
    user_id: int,
    data: PasswordReset,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[None]:
    """不需要原密码（学生忘记密码的场景）；与原密码相同时 service 会拒绝。"""
    user_services.reset_password(db, user_id, data)
    return Response.success(message="密码已重置")


@router.delete(
    "/{user_id}",
    response_model=Response[dict[str, int]],
    summary="删除用户（管理员）",
)
def delete_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[dict[str, int]]:
    """不能删自己；存在预约记录的用户会被拒绝（409），避免产生孤儿数据。"""
    user_services.delete_user(db, user_id, operator=admin)
    return Response.success(message="用户已删除", data={"id": user_id})
