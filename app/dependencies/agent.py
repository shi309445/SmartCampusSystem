"""Agent 相关依赖：拿 agent、拼 AgentContext

**agent 什么时候建**

`app/main.py` 的 lifespan 里建一次，放进 `app.state.agent` 复用。
agent 本身无状态（状态全在 messages 里），所以每次 `/api/chat` 直接复用同一个实例，
既省掉重建，也不会串用户（每个请求自带 context + thread_id）。

lifespan 里没建成功（比如启动时没配好模型）时，这里会懒加载重试一次；
再失败就回 503，而不是让每个请求都堆一层堆栈。
"""

from __future__ import annotations

from typing import Any

import loguru
from fastapi import Request

from app.common.exceptions import BusinessException
from app.harness.agent import build_agent
from app.harness.context import AgentContext
from app.schemas.table import User

logger = loguru.logger.bind(name=__name__)

__all__ = ["get_agent", "make_context"]


def get_agent(request: Request) -> Any:
    """取进程内复用的 agent（没有就懒加载一次）"""
    agent = getattr(request.app.state, "agent", None)
    if agent is not None:
        return agent

    try:
        agent = build_agent()
    except Exception as exc:                     # noqa: BLE001
        logger.exception("智能助手构建失败")
        raise BusinessException("智能助手暂时不可用，请稍后再试", code=503) from exc

    request.app.state.agent = agent
    logger.info("智能助手已懒加载就绪")
    return agent


def make_context(
    user: User,
    *,
    session_id: str | None = None,
    force_rag: bool | None = None,
) -> AgentContext:
    """把登录用户落成 AgentContext（身份只从服务端来，模型碰不到）"""
    return AgentContext(
        user_id=user.id,
        user_name=user.name,
        user_role=user.role,
        session_id=session_id,
        force_rag=force_rag,
    )
