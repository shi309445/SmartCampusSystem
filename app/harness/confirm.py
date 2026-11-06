"""写操作确认闸门：**模型只能提议，学生确认后服务端才执行**

**为什么写操作要单独一道闸门**

预约 / 取消会改数据库，而且学生不一定能撤销（实验室预约要管理员审核，自习室座位
提交即占座）。只靠提示词约束「先问学生再提交」是不可靠的：模型可以从历史里学样、
可以一口气调两次、也可以在没问的情况下直接调用。所以这里把**执行权**从模型手里
拿走：

    make_reservation / cancel_my_reservation
        → 中间件（write_confirmation_middleware）拦下，返回「待确认」ToolMessage
        → 模型只能把这句话转述给学生
        → 学生确认后，由**服务端**调用本模块的 confirm_action() 真正落库

**状态放在哪：会话记忆里，不另建表**

待确认动作就是历史里的一条 ToolMessage（`additional_kwargs.pending_action`），
跟着会话一起落在 Milvus（app/memory）。所以确认接口只需要
`(thread_id, tool_call_id)` 两个参数：

    * 服务重启 / 多进程 / 多副本都不会丢待确认动作；
    * 不用在服务端维护「待确认队列」，也就不用处理队列与历史不一致的问题。

**FastAPI 的用法（两阶段，无状态）**

    POST /chat
        result = chat(msg, context=AgentContext(user_id=..., session_id=...))
        if result.pending_actions:
            return {"status": "need_confirm", "pending": [a.as_dict() for a in result.pending_actions],
                    "answer": result.answer}
        return {"status": "ok", "answer": result.answer, "thread_id": result.thread_id}

    POST /chat/confirm
        body = {"thread_id": ..., "tool_call_id": ..., "approve": true, "action": {...可选...}}
        result = confirm_action(user_id=current_user.id, user_name=current_user.name, ...)
        return result.as_dict()

`action` 是兜底：调用方把 `pending_actions[0].as_dict()` 原样带回来，即使记忆层
不可用（Milvus 挂了）也能完成确认 —— 身份与权限仍然由 JWT + 业务层的归属校验兜住。

**幂等**

执行完会用 `replace_tool_result` 把那条「待确认」ToolMessage 原地换成执行结果
（不能追加第二条：同一个 tool_call_id 回两条 ToolMessage 会被模型 API 拒绝）。
所以重复调用 confirm_action 会命中「已执行」分支，返回第一次的结果，不会重复下单。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

import loguru
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.memory import (
    append_messages,
    load_all,
    replace_tool_result,
    thread_owner,
)
from app.tool.reservation_tool import (
    WRITE_TOOL_NAMES,
    cancel_reservation_for_user,
    describe_write_action,
    submit_reservation,
)
from app.utils.trace import set_trace_context

logger = loguru.logger.bind(name=__name__)

__all__ = [
    "PendingAction",
    "ConfirmResult",
    "extract_pending_actions",
    "confirm_action",
]

ConfirmStatus = Literal["approved", "rejected", "not_found", "forbidden", "error"]


@dataclass
class PendingAction:
    """一个「等学生点头」的写操作（从 ToolMessage 的 pending_action 还原）"""

    tool_call_id: str
    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)
    summary: str = ""

    def as_dict(self) -> dict[str, Any]:
        """给 HTTP 层直接序列化用（前端拿它渲染「确认卡片」）"""
        return {
            "requires_confirmation": True,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "args": self.args,
            "summary": self.summary,
        }

    @classmethod
    def from_message(cls, message: ToolMessage) -> "PendingAction | None":
        payload = (message.additional_kwargs or {}).get("pending_action")
        if not isinstance(payload, Mapping) or not payload.get("requires_confirmation"):
            return None
        return cls(
            tool_call_id=str(message.tool_call_id or payload.get("tool_call_id") or ""),
            tool_name=str(payload.get("tool_name") or message.name or ""),
            args=dict(payload.get("args") or {}),
            summary=str(payload.get("summary") or ""),
        )


@dataclass
class ConfirmResult:
    """确认接口的返回值（status 决定 HTTP 层怎么回：200 / 409 / 403 ...）"""

    status: ConfirmStatus
    answer: str
    tool_name: str = ""
    summary: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "approved"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ok": self.ok,
            "answer": self.answer,
            "tool_name": self.tool_name,
            "summary": self.summary,
            "detail": self.detail,
        }


def extract_pending_actions(messages: Sequence[BaseMessage]) -> list[PendingAction]:
    """从一轮的消息里挑出「等确认」的写操作（临时消息会被忽略）"""
    pending: list[PendingAction] = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        action = PendingAction.from_message(message)
        if action is not None:
            pending.append(action)
    return pending


def _find_action(
    messages: Sequence[BaseMessage], tool_call_id: str
) -> tuple[PendingAction | None, str | None, str | None]:
    """在历史里定位这条 tool_call：返回 (待确认动作, 已经执行过的结果, 上次的结论)"""
    call_args: dict[str, Any] = {}
    call_name = ""
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls or []:
                if str(call.get("id") or "") == str(tool_call_id):
                    call_args = dict(call.get("args") or {})
                    call_name = str(call.get("name") or "")

    pending: PendingAction | None = None
    done: str | None = None
    outcome: str | None = None
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        if str(message.tool_call_id or "") != str(tool_call_id):
            continue
        additional = message.additional_kwargs or {}
        action = PendingAction.from_message(message)
        if action is not None:
            pending = action
        else:
            done = str(message.content or "")
            outcome = str(additional.get("outcome") or "")

    if pending is not None:
        if not pending.args:
            pending.args = call_args
        if not pending.tool_name:
            pending.tool_name = call_name
    elif call_name in WRITE_TOOL_NAMES and done is None:
        # 历史里只有 tool_call、没有 ToolMessage（例如记忆被裁剪了一半）：
        # 仍然算一个可确认的动作，参数从 tool_call 里取。
        pending = PendingAction(
            tool_call_id=str(tool_call_id),
            tool_name=call_name,
            args=call_args,
            summary=describe_write_action(call_name, call_args),
        )
    return pending, done, outcome


def _execute(
    action: PendingAction, *, user_id: int, user_name: str, user_role: str
) -> str:
    """真正落库（身份是显式参数，模型碰不到）"""
    args = dict(action.args)
    if action.tool_name == "make_reservation":
        allowed = (
            "affair_name",
            "reserve_start_time",
            "reserve_end_time",
            "lab_id",
            "seat_id",
            "reserve_date",
            "people_num",
            "purpose",
            "auto_approve",
        )
        kwargs = {key: value for key, value in args.items() if key in allowed}
        return submit_reservation(
            user_id=int(user_id),
            user_name=user_name,
            user_role=user_role,
            **kwargs,                       # type: ignore[arg-type]
        )

    if action.tool_name == "cancel_my_reservation":
        try:
            reservation_id = int(args.get("reservation_id") or 0)
        except (TypeError, ValueError):
            reservation_id = 0
        return cancel_reservation_for_user(
            user_id=int(user_id),
            user_role=user_role,
            affair_name=str(args.get("affair_name") or ""),
            reservation_id=reservation_id,
            reason=str(args["reason"]) if args.get("reason") else None,
        )

    return f"暂不支持的写操作 {action.tool_name!r}，已忽略。"


def confirm_action(
    *,
    tool_call_id: str,
    user_id: int | None,
    approve: bool = True,
    thread_id: str | None = None,
    user_name: str = "",
    user_role: str = "student",
    action: Mapping[str, Any] | None = None,
    trace_id: str | None = None,
) -> ConfirmResult:
    """确认（或拒绝）一个待提交的写操作

    :param tool_call_id: 待确认动作的 id（`ChatResult.pending_actions[0].tool_call_id`）
    :param user_id: 当前登录学生（必填；写操作不能匿名）
    :param approve: True=提交，False=取消（取消也要落一条「已取消」进会话）
    :param thread_id: 会话 id；传了就校验归属，并把结果写回会话
    :param action: 兜底回放（{"tool_name":..., "args":...}），记忆不可用时也能确认
    :param trace_id: 复用 HTTP 中间件生成的 trace_id（不传就新建）
    """
    if user_id is None:
        return ConfirmResult(
            "forbidden", "需要登录后才能确认这次操作。", detail={"reason": "not_logged_in"}
        )

    # 学生确认单独一个 trace：和「提议那一轮」的 trace_id 区分开，但都能按 user/thread 搜到
    trace_id = set_trace_context(trace_id, user_id=user_id, thread_id=thread_id)

    owner = thread_owner(thread_id) if thread_id else None
    if thread_id:
        if owner is None:
            logger.warning("拒绝确认：thread_id 格式不合法 {}", thread_id)
            return ConfirmResult(
                "forbidden",
                "会话 id 不合法，无法确认。",
                detail={"reason": "bad_thread_id"},
            )
        if owner != int(user_id):
            logger.warning("拒绝越权确认：thread={} owner={} user={}", thread_id, owner, user_id)
            return ConfirmResult(
                "forbidden", "这个会话不属于当前账号，无法确认。", detail={"reason": "thread_mismatch"}
            )

    if not tool_call_id:
        return ConfirmResult("error", "缺少待确认操作的 id。", detail={"reason": "missing_tool_call_id"})

    messages = load_all(thread_id, user_id=user_id) if thread_id else []
    pending, done, last_outcome = _find_action(messages, tool_call_id)

    if pending is None:
        if done is not None:                # 已经处理过：幂等返回第一次的结论
            return ConfirmResult(
                "rejected" if last_outcome == "rejected" else "approved",
                done,
                detail={"idempotent": True, "replayed": True},
            )
        if action is None:
            return ConfirmResult(
                "not_found",
                "没有找到这条待确认的操作，可能已经处理过了，请刷新后重试。",
                detail={"thread_id": thread_id},
            )
        # 回放兜底：调用方把 pending_actions 原样传回
        name = str(action.get("tool_name") or "")
        if name not in WRITE_TOOL_NAMES:
            return ConfirmResult("error", f"{name!r} 不是可确认的写操作。", detail={"reason": "bad_tool"})
        args = dict(action.get("args") or {})
        pending = PendingAction(
            tool_call_id=str(tool_call_id),
            tool_name=name,
            args=args,
            summary=describe_write_action(name, args),
        )

    if pending.tool_name not in WRITE_TOOL_NAMES:
        return ConfirmResult(
            "error",
            f"{pending.tool_name!r} 不是可确认的写操作。",
            detail={"reason": "bad_tool"},
        )

    # ---------------------------------------------------------- 学生拒绝
    if not approve:
        content = "学生没有确认，本次操作已取消，没有提交。"
        if thread_id:
            replace_tool_result(
                thread_id,
                tool_call_id,
                content=content,
                name=pending.tool_name,
                outcome="rejected",
            )
        logger.info("学生取消写操作：{} {}", pending.tool_name, pending.summary)
        return ConfirmResult(
            "rejected",
            "好的，这次操作已取消，没有提交。",
            tool_name=pending.tool_name,
            summary=pending.summary,
        )

    # ---------------------------------------------------------- 学生确认 → 执行
    try:
        answer = _execute(
            pending, user_id=int(user_id), user_name=user_name, user_role=user_role
        )
    except Exception:                      # noqa: BLE001 —— 确认接口不能 500
        logger.exception("确认写操作失败：{} {}", pending.tool_name, pending.summary)
        return ConfirmResult(
            "error",
            "这次操作提交时出错了，请稍后再试。",
            tool_name=pending.tool_name,
            summary=pending.summary,
        )

    if thread_id:
        replace_tool_result(
            thread_id,
            tool_call_id,
            content=answer,
            name=pending.tool_name,
            outcome="approved",
        )
        # 补一条 AI 消息，让历史读起来是完整的一问一答（下一轮模型能看到结果）
        append_messages(
            thread_id, user_id, [AIMessage(content=f"已按你的确认执行：{answer}")]
        )

    logger.info("学生确认并执行写操作：{} {}", pending.tool_name, pending.summary)
    return ConfirmResult(
        "approved",
        answer,
        tool_name=pending.tool_name,
        summary=pending.summary,
        detail={"thread_id": thread_id, "tool_call_id": tool_call_id, "trace_id": trace_id},
    )


# ---------------------------------------------------------------- 自检


def _self_check() -> None:  # pragma: no cover - 脚本式自检
    """不连模型、不连业务库，验证「拦 → 待确认 → 同意/拒绝 → 幂等」整条链路

        .\\.venv\\Scripts\\python.exe -m app.harness.confirm
    """
    from types import SimpleNamespace

    from app.harness.middleware import write_confirmation_middleware
    from app.memory import append_messages, clear_thread, load_all, make_thread_id

    # 装饰器把函数变成了中间件对象，真正可调用的是它的 wrap_tool_call
    gate_fn = write_confirmation_middleware.wrap_tool_call

    thread_id = make_thread_id(990002, "confirm-selftest")
    clear_thread(thread_id)
    user_id = 990002

    # ---------------------------------------------------------- ① 中间件硬闸门
    called = []

    def _handler(_request):                      # 写操作绝不该走到这里
        called.append(True)
        return ToolMessage(content="不该执行", tool_call_id="x")

    def _gate(tool_name: str, args: dict, user: int | None = user_id):
        request = SimpleNamespace(
            tool_call={"name": tool_name, "id": f"call_{tool_name}", "args": args},
            runtime=SimpleNamespace(context=SimpleNamespace(user_id=user)),
            state={"messages": []},
        )
        return gate_fn(request, _handler)

    gate = _gate(
        "make_reservation",
        {"affair_name": "lab", "lab_id": 3, "reserve_start_time": "14:00",
         "reserve_end_time": "16:00", "reserve_date": "2026-10-16"},
    )
    assert not called, "写操作被直接执行了 —— 闸门失效"
    assert gate.additional_kwargs["pending_action"]["requires_confirmation"]
    logger.info("闸门拦住写操作：{}", gate.additional_kwargs["pending_action"]["summary"])

    bad = _gate("make_reservation", {"affair_name": "lab", "reserve_start_time": "14:00",
                                     "reserve_end_time": "16:00"})
    assert bad.status == "error" and "lab_id" in bad.content
    logger.info("参数不完整被提前拦住：{}", bad.content[:40])

    anonymous = _gate("make_reservation", {"affair_name": "lab", "lab_id": 3,
                                           "reserve_start_time": "14:00",
                                           "reserve_end_time": "16:00"}, user=None)
    assert anonymous.status == "error" and "登录" in anonymous.content
    logger.info("未登录被拦住：{}", anonymous.content[:30])

    # ---------------------------------------------------------- ② 待确认动作落库
    pending_action = gate.additional_kwargs["pending_action"]

    def _pending_message(tool_call_id: str) -> ToolMessage:
        return gate.model_copy(update={"tool_call_id": tool_call_id})

    append_messages(thread_id, user_id, [
        HumanMessage(content="帮我约明天下午的实验室"),
        AIMessage(content="", tool_calls=[{
            "name": "make_reservation",
            "args": pending_action["args"],
            "id": "call_make_reservation",
            "type": "tool_call",
        }]),
        _pending_message("call_make_reservation"),
    ])
    actions = extract_pending_actions(load_all(thread_id, user_id=user_id))
    assert len(actions) == 1, f"应该读到 1 个待确认动作，实际 {len(actions)}"
    logger.info("从记忆里读到待确认动作：{}", actions[0].as_dict())

    # ---------------------------------------------------------- ③ 拒绝 + 幂等
    rejected = confirm_action(
        tool_call_id="call_make_reservation", user_id=user_id, approve=False,
        thread_id=thread_id,
    )
    assert rejected.status == "rejected"
    again = confirm_action(
        tool_call_id="call_make_reservation", user_id=user_id, approve=False,
        thread_id=thread_id,
    )
    assert again.status == "rejected" and again.detail.get("idempotent"), "重复确认识别失败"
    assert not extract_pending_actions(load_all(thread_id, user_id=user_id)), "拒绝后仍有待确认"
    logger.info("拒绝 → 幂等重放：{} / {}", rejected.answer, again.status)

    # ---------------------------------------------------------- ④ 越权 / 找不到
    forbidden = confirm_action(
        tool_call_id="call_make_reservation", user_id=1, approve=True, thread_id=thread_id
    )
    assert forbidden.status == "forbidden"
    missing = confirm_action(
        tool_call_id="call_不存在", user_id=user_id, approve=True, thread_id=thread_id
    )
    assert missing.status == "not_found"
    logger.info("越权 / 找不到：{} / {}", forbidden.status, missing.status)

    # ---------------------------------------------------------- ⑤ 同意 → 执行
    # 用一个假的 submit_reservation 替掉真实落库（自检不该往业务库里写东西）
    global submit_reservation
    real_submit = submit_reservation
    submit_reservation = lambda **kwargs: f"模拟成功：{kwargs.get('lab_id')} 号实验室"
    try:
        append_messages(thread_id, user_id, [
            HumanMessage(content="再帮我约一个"),
            AIMessage(content="", tool_calls=[{
                "name": "make_reservation",
                "args": pending_action["args"],
                "id": "call_approve",
                "type": "tool_call",
            }]),
            _pending_message("call_approve"),
        ])
        approved = confirm_action(
            tool_call_id="call_approve", user_id=user_id, approve=True,
            thread_id=thread_id, user_name="自检同学",
        )
        assert approved.status == "approved" and approved.answer.startswith("模拟成功")
        replay = confirm_action(
            tool_call_id="call_approve", user_id=user_id, approve=True, thread_id=thread_id
        )
        assert replay.detail.get("idempotent") and replay.answer == approved.answer
        logger.info("同意 → 执行：{}（重放幂等：{}）", approved.answer, replay.detail)
    finally:
        submit_reservation = real_submit

    history = load_all(thread_id, user_id=user_id)
    assert not extract_pending_actions(history), "执行后应不再有待确认"
    assert any("已按你的确认执行" in str(m.content) for m in history), "缺少执行回执"
    logger.info("最终会话 {} 条，待确认 0 条", len(history))

    clear_thread(thread_id)
    logger.info("写操作确认自检通过")


if __name__ == "__main__":
    _self_check()
