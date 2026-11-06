"""app.dependencies —— 路由依赖

    auth.py       JWT → 当前用户 / 管理员
    agent.py      agent 实例与 AgentContext 的组装
    rate_limit.py /api/chat 的频率限制
"""

from app.dependencies.auth import get_current_admin, get_current_user, oauth2_scheme, require_admin
from app.dependencies.agent import get_agent, make_context
from app.dependencies.rate_limit import chat_rate_limit

__all__ = [
    "chat_rate_limit",
    "get_agent",
    "get_current_admin",
    "get_current_user",
    "make_context",
    "oauth2_scheme",
    "require_admin",
]
