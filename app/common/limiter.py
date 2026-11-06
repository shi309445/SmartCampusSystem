"""进程内滑动窗口限流（单机够用，不引入 Redis）

**为什么需要**

日 token 配额（app/harness/budget.py）管的是「一天花多少钱」，管不住
「一秒钟发 100 条请求」——那种场景下模型还没开始计费，网关和数据库已经被打满。
这里补的是**频率**这一档。

**为什么不用 Redis**

当前是单进程部署（uvicorn 单 worker）。上多副本时把 `SlidingWindowLimiter`
换成 Redis 的 `INCR + EXPIRE` 即可，接口（`hit` 返回 (是否放行, 重试秒数)）不用变。
"""

from __future__ import annotations

import threading
import time
from collections import deque

__all__ = ["SlidingWindowLimiter"]


class SlidingWindowLimiter:
    """按 key 的滑动窗口：window_seconds 秒内最多 limit 次

    每个 key 保存一串时间戳，每次请求先丢掉窗口外的，再看剩余的有没有到上限。
    内存占用 = 窗口内请求数（被 limit 卡住，不会无限涨）。
    """

    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = int(limit)
        self.window = max(1.0, float(window_seconds))
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> tuple[bool, int]:
        """记一次请求；返回 (是否放行, 建议等待秒数)

        limit <= 0 视为不限流。
        """
        if self.limit <= 0:
            return True, 0

        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                retry_after = max(1, int(self.window - (now - hits[0])) + 1)
                return False, retry_after
            hits.append(now)
            return True, 0

    def reset(self, key: str | None = None) -> None:
        """清空某个 key（自检用）或全部"""
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)
