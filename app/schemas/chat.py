"""聊天接口的数据模型（HTTP 层）

和 Agent 内部模型的分工：

    ChatResult / PendingAction（app/harness）  —— 内部结果对象
    ChatRequest / ChatResponse（本文件）        —— 对前端承诺的 JSON 契约

两者刻意不共用：内部改字段不该悄悄改变前端契约，反之亦然。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.models import RequestBase

__all__ = [
    "PendingActionOut",
    "DecisionBrief",
    "ChatRequest",
    "ChatResponse",
    "ConfirmRequest",
    "ConfirmResponse",
    "HistoryMessage",
    "HistoryResponse",
    "ThreadInfo",
    "UsageOut",
]


class PendingActionOut(BaseModel):
    """一个「等学生确认」的写操作（前端据此渲染确认卡片）"""

    tool_call_id: str = Field(..., description="回传给 /api/chat/confirm 的 id")
    tool_name: str = Field(..., description="make_reservation / cancel_my_reservation")
    summary: str = Field("", description="一句话说明（直接展示给学生）")
    args: dict[str, Any] = Field(default_factory=dict, description="原始参数（只读展示）")
    requires_confirmation: bool = True


class DecisionBrief(BaseModel):
    """决策层结论的精简版（前端/排查用，不需要完整规则命中细节）"""

    stage: str | None = Field(None, description="rule_hit / intent_denied / ...")
    allow_rag: bool | None = Field(None, description="本轮是否放行了知识库检索")


class ChatRequest(RequestBase):
    """发一条消息"""

    message: str = Field(..., min_length=1, max_length=2000, description="学生消息")
    session_id: str | None = Field(
        None, max_length=64,
        description="会话 id，可选；不传 = 该学生的一条长会话（u{user_id}）",
    )
    force_rag: bool | None = Field(
        None, description="调试用：true 强制查知识库 / false 强制不查；不传走正常决策",
    )


class ChatResponse(BaseModel):
    """一轮问答的结果

    `status == "need_confirm"` 时，学生必须先调 /api/chat/confirm，
    在确认之前 answer 里的操作**没有提交**。
    """

    status: Literal["ok", "need_confirm"]
    answer: str
    thread_id: str | None = None
    trace_id: str | None = Field(None, description="排障 id，同时出现在响应头 X-Trace-Id")
    tokens: int = Field(0, description="本轮消耗 token")
    decision: DecisionBrief | None = None
    pending_actions: list[PendingActionOut] = Field(default_factory=list)
    needs_confirmation: bool = False
    # 主模型不可用、本轮走了降级（备用模型或兜底话术）。默认 false，不影响老前端。
    degraded: bool = Field(
        False, description="本轮是否降级（主模型不可用时启用备用模型或兜底话术）"
    )


class ConfirmRequest(RequestBase):
    """确认 / 拒绝一个待提交的写操作"""

    thread_id: str = Field(..., min_length=1, max_length=256)
    tool_call_id: str = Field(..., min_length=1, max_length=128)
    approve: bool = Field(True, description="True=提交，False=取消")
    action: dict[str, Any] | None = Field(
        None, description="兜底回放：把 ChatResponse.pending_actions[0] 原样带回来",
    )


class ConfirmResponse(BaseModel):
    status: Literal["approved", "rejected", "not_found", "forbidden", "error"]
    ok: bool
    answer: str
    tool_name: str = ""
    summary: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class HistoryMessage(BaseModel):
    """历史消息（存储行 → 前端可展示）"""

    role: Literal["human", "ai", "tool", "system"]
    content: str = ""
    seq: int = 0
    created_at: datetime | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    pending: bool = Field(False, description="是不是还没确认的写操作")


class HistoryResponse(BaseModel):
    thread_id: str
    total: int
    messages: list[HistoryMessage] = Field(default_factory=list)


class ThreadInfo(BaseModel):
    """一个会话的摘要（历史会话列表用）"""

    thread_id: str
    session: str | None = None
    messages: int = 0
    last_at: datetime | None = None


class UsageOut(BaseModel):
    """某个学生今天的用量（管理员查看 / 排查预算用）"""

    user_id: int
    date: date
    tokens: int
    calls: int
    daily_tokens_limit: int
    daily_calls_limit: int
