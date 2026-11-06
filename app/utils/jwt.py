"""JWT 令牌工具（全项目通用）

只负责「签发 / 解析」这一件事，不查数据库、不带业务规则；
业务规则（账号是否存在、是否被禁用、是否管理员）在 app/services/user 里做。

约定：
    * payload 只放 user_id，角色 / 状态每次请求都从数据库读，
      这样管理员改了角色或禁用了账号，能立刻生效，不用等 token 过期
    * exp / iat 一律用 UTC：JWT 的 exp 按 UTC 校验，用本地时间会整体偏差一个时区
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.config import settings


def access_token_ttl_seconds() -> int:
    """当前配置下 token 的有效期（秒），用于返回给前端"""
    return int(settings.JWT_EXPIRE_HOURS * 3600)


def create_access_token(user_id: int, *, expires_hours: int | None = None) -> str:
    """签发访问令牌

    expires_hours: 覆盖默认有效期（测试里传负数即可造一个已过期的 token）
    """
    hours = settings.JWT_EXPIRE_HOURS if expires_hours is None else expires_hours
    now = datetime.now(timezone.utc)

    payload: dict[str, Any] = {
        "user_id": user_id,
        "iat": now,
        "exp": now + timedelta(hours=hours),  # exp 是 JWT 的固定声明，不能改名
    }
    return jwt.encode(
        payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )


def decode_access_token(token: str) -> dict[str, Any]:
    """解析并校验令牌，返回 payload

    签名不对 / 已过期 / 缺少 exp 都会抛 jwt.PyJWTError 子类，
    由调用方（app/services/user/auth_services.py）统一转成业务异常。
    """
    return jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        options={"require": ["exp"]},  # 没有 exp 声明的令牌一律拒绝
        leeway=timedelta(seconds=10),  # 容忍服务器之间的时钟偏移
    )
