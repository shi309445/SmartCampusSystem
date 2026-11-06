"""限流依赖：/api/chat 每个学生每分钟最多 N 条（N 来自配置，0 = 不限）"""

from __future__ import annotations

import threading

from fastapi import Depends

from app.common.exceptions import BusinessException
from app.common.limiter import SlidingWindowLimiter
from app.config import settings
from app.dependencies.auth import get_current_user
from app.schemas.table import User

__all__ = ["chat_rate_limit", "reset_rate_limits"]

_lock = threading.Lock()
_limiter: SlidingWindowLimiter | None = None
_limiter_limit = -1


def _get_limiter() -> SlidingWindowLimiter:
    """进程内单例；配置值变化时重建（方便测试 / 运行时调参）"""
    global _limiter, _limiter_limit
    limit = int(settings.CHAT_RATE_LIMIT_PER_MINUTE)
    with _lock:
        if _limiter is None or _limiter_limit != limit:
            _limiter = SlidingWindowLimiter(limit=limit, window_seconds=60.0)
            _limiter_limit = limit
        return _limiter


def chat_rate_limit(user: User = Depends(get_current_user)) -> None:
    """按用户 id 限流；超限抛 429（统一信封 + Retry-After）"""
    allowed, retry_after = _get_limiter().hit(str(user.id))
    if allowed:
        return

    raise BusinessException(
        f"发送太频繁了，请 {retry_after} 秒后再试",
        code=429,
    )


def reset_rate_limits() -> None:
    """清空限流状态（自检 / 测试用）"""
    _get_limiter().reset()
