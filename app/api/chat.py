"""智能助手接口：发消息 / 确认写操作 / 历史 / 会话管理

**两阶段写操作（前端必须按这个流程）**

    POST /api/chat
        → status = "ok"           直接拿到回答
        → status = "need_confirm" 回答里是「待确认」，还没有提交
              ↓ 学生点「确认」
    POST /api/chat/confirm {thread_id, tool_call_id, approve: true}
        → status = "approved"     已提交（预约单号在 answer 里）

**为什么确认要单独一个接口**

预约/取消是写操作，执行权不能给模型（见 app/harness/confirm.py）。
确认请求必须带登录令牌，服务端还会校验 thread_id 属于这个学生，
所以模型没法自己「确认」自己提议的操作。

**线程池**

`chat()` / `confirm_action()` / `clear_thread()` 都是同步阻塞调用（模型、MySQL、
Milvus），而路由是 async 的，所以统一用 `run_in_threadpool` 丢给线程池，
不阻塞事件循环。trace_id 在请求上下文里先生成好再传进去，保证 HTTP 日志、
agent 日志、LangSmith run 是同一个 trace。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import loguru
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.common.response import Response
from app.dependencies.agent import get_agent, make_context
from app.dependencies.auth import get_current_user
from app.dependencies.rate_limit import chat_rate_limit
from app.harness.agent import chat
from app.harness.confirm import confirm_action
from app.memory import clear_thread, list_threads, load_records, make_thread_id, thread_session
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ConfirmRequest,
    ConfirmResponse,
    DecisionBrief,
    HistoryMessage,
    HistoryResponse,
    PendingActionOut,
    ThreadInfo,
)
from app.schemas.table import User
from app.utils.trace import get_trace_context, set_trace_context

logger = loguru.logger.bind(name=__name__)

router = APIRouter(prefix="/api/chat", tags=["智能助手"])

__all__ = ["router"]

# confirm 的业务状态 → HTTP 状态码（前端可以按状态码做统一处理）
_CONFIRM_HTTP_STATUS = {
    "approved": 200,
    "rejected": 200,
    "not_found": 404,
    "forbidden": 403,
    "error": 500,
}


def _current_trace(user: User) -> str:
    """复用请求中间件生成的 trace_id（没有就新建），并把 user 补进上下文"""
    return set_trace_context(
        get_trace_context().get("trace_id"), user_id=user.id, thread_id=None
    )


def _to_datetime(ms: Any) -> datetime | None:
    """毫秒时间戳 → datetime（存储层统一存毫秒整数）"""
    try:
        value = int(ms or 0)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return datetime.fromtimestamp(value / 1000)


def _to_history_message(row: dict[str, Any]) -> HistoryMessage:
    payload = row.get("payload") or {}
    if isinstance(payload, str):                 # 兜底：JSON 字段被读成字符串
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}

    role = row.get("role")
    if role not in ("human", "ai", "tool", "system"):
        role = "ai"

    return HistoryMessage(
        role=role,
        content=str(row.get("content") or ""),
        seq=int(row.get("seq") or 0),
        created_at=_to_datetime(row.get("created_at")),
        tool_name=payload.get("name") or None,
        tool_call_id=payload.get("tool_call_id") or None,
        pending=bool(payload.get("pending_action")),
    )


@router.post("", response_model=Response[ChatResponse], summary="发一条消息给智能助手")
async def send_message(
    data: ChatRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    _: None = Depends(chat_rate_limit),
) -> Response[ChatResponse]:
    """一轮问答；有写操作时返回 need_confirm，必须再调 /api/chat/confirm"""
    agent = get_agent(request)
    context = make_context(
        current_user, session_id=data.session_id, force_rag=data.force_rag
    )
    trace_id = _current_trace(current_user)

    result = await run_in_threadpool(
        chat,
        data.message,
        context=context,
        agent=agent,
        trace_id=trace_id,
    )

    pending = [PendingActionOut(**action.as_dict()) for action in result.pending_actions]
    decision = (
        DecisionBrief(stage=result.decision.stage, allow_rag=result.decision.allow_rag)
        if result.decision is not None
        else None
    )
    payload = ChatResponse(
        status="need_confirm" if pending else "ok",
        answer=result.answer,
        thread_id=result.thread_id,
        trace_id=result.trace_id,
        tokens=result.tokens,
        decision=decision,
        pending_actions=pending,
        needs_confirmation=bool(pending),
        degraded=getattr(result, "degraded", False),
    )
    return Response.success(data=payload)


@router.post(
    "/confirm",
    response_model=Response[ConfirmResponse],
    summary="确认 / 拒绝一个待提交的写操作",
)
async def confirm(
    data: ConfirmRequest,
    current_user: User = Depends(get_current_user),
):
    """学生确认后才真正落库；重复确认是幂等的（返回第一次的结果）"""
    trace_id = _current_trace(current_user)
    result = await run_in_threadpool(
        confirm_action,
        tool_call_id=data.tool_call_id,
        user_id=current_user.id,
        approve=data.approve,
        thread_id=data.thread_id,
        user_name=current_user.name,
        user_role=current_user.role,
        action=data.action,
        trace_id=trace_id,
    )

    payload = ConfirmResponse(**result.as_dict())
    if result.status in ("approved", "rejected"):
        message = "操作已提交" if result.status == "approved" else "操作已取消"
        return Response.success(message=message, data=payload)

    code = _CONFIRM_HTTP_STATUS.get(result.status, 500)
    return JSONResponse(
        status_code=code,
        content=Response.error(code=code, message=result.answer, data=payload).model_dump(),
    )


@router.get("/history", response_model=Response[HistoryResponse], summary="会话历史")
def history(
    session_id: str | None = Query(None, max_length=64, description="不传 = 默认长会话"),
    limit: int = Query(50, ge=1, le=200, description="最多返回最近多少条"),
    current_user: User = Depends(get_current_user),
) -> Response[HistoryResponse]:
    """只返回当前学生自己的会话（thread_id 由 user_id 拼出来）"""
    thread_id = make_thread_id(current_user.id, session_id)
    rows = load_records(thread_id, user_id=current_user.id)
    messages = [_to_history_message(row) for row in rows[-limit:]]
    return Response.success(
        data=HistoryResponse(thread_id=thread_id, total=len(rows), messages=messages)
    )


@router.get("/threads", response_model=Response[list[ThreadInfo]], summary="我的会话列表")
def threads(
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
) -> Response[list[ThreadInfo]]:
    items = list_threads(current_user.id, limit=limit)
    data = [
        ThreadInfo(
            thread_id=str(item["thread_id"]),
            session=thread_session(str(item["thread_id"])),
            messages=int(item.get("messages") or 0),
            last_at=_to_datetime(item.get("last_at")),
        )
        for item in items
    ]
    return Response.success(data=data)


@router.delete("/thread", response_model=Response[dict[str, Any]], summary="清空会话")
async def delete_thread(
    session_id: str | None = Query(None, max_length=64, description="不传 = 默认长会话"),
    current_user: User = Depends(get_current_user),
):
    """学生的「新会话」/ 隐私清理：删除该会话的全部消息"""
    thread_id = make_thread_id(current_user.id, session_id)
    deleted = await run_in_threadpool(
        clear_thread, thread_id, user_id=current_user.id
    )
    logger.info("会话已清空：{}（{} 条）", thread_id, deleted)
    return Response.success(
        message="会话已清空",
        data={"thread_id": thread_id, "deleted": deleted},
    )
