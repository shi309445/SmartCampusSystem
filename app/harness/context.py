"""调用上下文（AgentContext）

**为什么单独一个文件**

AgentContext 是「主 Agent 与工具之间的契约」：调用方（Web 层 / CLI）把它传进
`chat(context=...)`，中间件和工具都从 `runtime.context` 读它。

它不能留在 app/harness/agent.py 里 —— agent.py 要 import app.tool（拿工具列表），
而 app/tool/reservation_tool.py 又要用 `ToolRuntime[AgentContext]` 标注自己接收的
运行时对象。两边互相 import 就成了循环。把这份纯数据类拆出来，
app.tool 和 app.harness.agent 都只单向依赖它，环就断开了。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["AgentContext", "ContextSchema"]


@dataclass
class AgentContext:
    """调用时传入的上下文（不是对话历史，历史走 messages）

    - `user_id` / `user_name`：预约类工具靠它确定「预约人是谁」。
      工具从 context 取身份（app/tool/reservation_tool.py 的 `_identity`），
      模型既填不了也改不了 —— 既不能替别人预约，也不会伪造申请人姓名。
      未登录时保持 None / ""，预约工具会回一句「请先登录」。
    - `user_role`：角色，直接取用户表的 `role`（student / admin）。业务层用它判越权
      （如「取消别人的预约」只有 admin 能做），也是六层图里“能力隔离 / 数据分级”的
      落点；接口层由 JWT 换出来的用户对象填充，模型改不了。
    - `thread_id` / `session_id`：会话记忆用（app/memory）。
      `thread_id` 优先级最高（由调用方显式指定，格式必须是 `u{user_id}` 或
      `u{user_id}:{session_id}`，见 `app/memory/short_term.py` 的 make_thread_id）；
      只给 `session_id` 时由 `chat()` 用 user_id 拼出来。两者都不给 = 一个学生
      一条长会话（`u{user_id}`）。未登录（user_id 为空）时记忆自动关闭。
    - `force_rag`：调试与接口层用，强行放行（True）或否决（False）本轮检索；
      为 None 时走正常的决策层（规则层 → 轻量模型 → 主模型）
    """

    user_id: int | None = None
    user_name: str = ""
    user_role: str = "student"
    force_rag: bool | None = None
    thread_id: str | None = None
    session_id: str | None = None


# 旧名兼容（旧版 ContextSchema 只有 user_role 一个字段）
ContextSchema = AgentContext
