"""trace_id 与请求上下文（ContextVar）

**为什么要这个**

现在排查一个学生的问题，要分别看：loguru 的一行日志、LangSmith 的一条 run、
MySQL 里的一行预约、Milvus 里的一段会话 —— 没有共同 id 就串不起来。

`trace_id` 就是这条串起来的线：

    chat() 入口生成 trace_id
        → 写进 ContextVar（日志 patcher 自动挂到每条日志）
        → 写进 LangSmith run 的 metadata（Dashboard 可按它搜）
        → 写进 ChatResult / HTTP 响应头 X-Trace-Id（学生报障时能直接给出来）
        → confirm_action 再生成一个（学生操作可单独追溯）

ContextVar 是**进程内、跟随线程/协程**的：FastAPI 的 async 路由里天然隔离，
同步路由跑在线程池里也能各自隔离（`asyncio.to_thread` / `run_in_threadpool`
会复制上下文；我们自己的工具线程池也显式 `copy_context()` 带过去）。
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator

__all__ = [
    "new_trace_id",
    "get_trace_context",
    "set_trace_context",
    "reset_trace_context",
    "trace_scope",
]

_trace_id: ContextVar[str | None] = ContextVar("agent_trace_id", default=None)
_user_id: ContextVar[int | None] = ContextVar("agent_trace_user_id", default=None)
_thread_id: ContextVar[str | None] = ContextVar("agent_trace_thread_id", default=None)


def new_trace_id() -> str:
    """16 位十六进制，够短好看、碰撞概率可忽略"""
    return uuid.uuid4().hex[:16]


def get_trace_context() -> dict[str, object]:
    return {
        "trace_id": _trace_id.get(),
        "user_id": _user_id.get(),
        "thread_id": _thread_id.get(),
    }


def set_trace_context(
    trace_id: str | None = None,
    *,
    user_id: int | None = None,
    thread_id: str | None = None,
) -> str:
    """设置当前上下文并返回 trace_id（不传就新生成一个）"""
    resolved = trace_id or new_trace_id()
    _trace_id.set(resolved)
    _user_id.set(user_id)
    _thread_id.set(thread_id)
    return resolved


def reset_trace_context(
    token_trace: Token | None = None,
    token_user: Token | None = None,
    token_thread: Token | None = None,
) -> None:
    """还原（自检 / 长跑循环里避免上下文串味）"""
    if token_trace is not None:
        _trace_id.reset(token_trace)
    if token_user is not None:
        _user_id.reset(token_user)
    if token_thread is not None:
        _thread_id.reset(token_thread)


@contextmanager
def trace_scope(
    trace_id: str | None = None,
    *,
    user_id: int | None = None,
    thread_id: str | None = None,
) -> Iterator[str]:
    """`with trace_scope(...) as trace_id:` —— 退出时自动还原

    FastAPI 以后可以直接包在请求中间件里；CLI / 脚本里也可以局部使用。
    """
    resolved = trace_id or new_trace_id()
    token_trace = _trace_id.set(resolved)
    token_user = _user_id.set(user_id)
    token_thread = _thread_id.set(thread_id)
    try:
        yield resolved
    finally:
        reset_trace_context(token_trace, token_user, token_thread)
