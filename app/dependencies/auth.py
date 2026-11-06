"""鉴权依赖：把 JWT 换成当前用户

沿用 app/services/user/auth_services.py 的业务规则（令牌无效 / 用户被删 / 被禁用
都会抛对应的 UserException，由 app/common/exceptions.py 统一转成 401/403）。

**为什么用 OAuth2PasswordBearer**

它负责一件事：从 `Authorization: Bearer <token>` 里取出令牌，并让 /docs 上出现
「Authorize」按钮。我们的 /api/auth/login 收的是 JSON（不是 OAuth2 的 form），
所以 tokenUrl 只用于文档展示，不影响实际鉴权。
"""

from __future__ import annotations

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.table import User
from app.services.user import auth_services
from app.services.user.exceptions import TokenInvalidError

__all__ = ["oauth2_scheme", "get_current_user", "get_current_admin", "require_admin"]

# auto_error=False：没带令牌时由我们自己抛 TokenInvalidError（复用同一套错误格式）
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """当前登录用户（每次请求都回库查，角色/禁用状态改完立刻生效）"""
    if not token:
        raise TokenInvalidError("未登录或登录已过期，请先登录")
    return auth_services.get_current_user_or_raise(db, token)


def get_current_admin(user: User = Depends(get_current_user)) -> User:
    """要求管理员身份（非管理员抛 UserPermissionError → 403）"""
    return auth_services.check_admin_or_raise(user)


# 语义化别名，路由里写 `user: User = Depends(require_admin)` 更直观
require_admin = get_current_admin
