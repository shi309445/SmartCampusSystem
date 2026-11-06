"""预约工具：实验室预约 + 图书馆自习室座位预约

**这个文件里的东西分三层**（对应「工具接口实现在 app/tool 下」的要求）：

    ① 执行层（prepare_* / execute_*）
       参数与 app/harness/graph.py 的输入状态（ReservationInput）字段一一对齐，
       给图节点调用：只负责「校验成请求模型 → 调业务服务 → 转成可序列化的 dict」，
       人看不见它，模型也看不见它。

    ② 查询层（search_labs / list_lab_slots / search_study_rooms / find_available_seats）
       给主 Agent 找 lab_id / seat_id 用 —— 预约必须落到具体对象上，
       不给查询工具的话模型只能瞎猜 id，那是最容易出一堆脏数据的失败方式。

    ③ 预约入口（make_reservation）
       主 Agent 拿到的**唯一一个预约工具**，内部把参数交给 app/harness/graph.py 的预约图，
       由图按 affair_name 分流到实验室 / 图书馆两条流水线。
       另有 list_my_reservations / cancel_my_reservation 两个配套工具（查自己的、取消自己的）。

       注意：make_reservation / cancel_my_reservation 是**写操作**，调用时会被中间件
       拦成「待确认」而不会立即执行（见 app/harness/middleware.py 的
       write_confirmation_middleware）：学生确认后由 app/harness/confirm.py 调
       submit_reservation / cancel_reservation_for_user 真正落库。
       这两个执行函数把身份做成显式参数，所以 CLI / FastAPI 都能直接调，不依赖
       LangChain 注入的 ToolRuntime。

**身份怎么进来的**

user_id / user_name / user_role **不是工具参数**，模型也填不了：
它们从 `ToolRuntime.context`（= app/harness/context.py 的 AgentContext）里读。
这样模型既不会伪造申请人，也不会出现「代别人预约」这种越权。
预约图的输入状态里同样带这三个字段，两者严格对齐。

**数据库会话**

一次工具调用开一个短事务（`_session()`），不把 Session 塞进图状态：
状态要保持可序列化（将来接 checkpointer / 回放 / 前端流式都要它）。
测试里可以把本模块的 `SessionLocal` 换成临时库的连接工厂。
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

import loguru
from langchain.tools import ToolRuntime, tool
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.harness.context import AgentContext
from app.services.laboratory import exceptions as lab_exc
from app.services.laboratory import lab_services
from app.services.laboratory.models import (
    ReservationCancel as LabReservationCancel,
    ReservationCreate as LabReservationCreate,
    ReservationResponse as LabReservationResponse,
)
from app.services.laboratory.table import RESERVATION_STATUS_TEXT as LAB_STATUS_TEXT
from app.services.laboratory.table import STATUS_NORMAL as LAB_STATUS_NORMAL
from app.services.library import exceptions as lib_exc
from app.services.library import library_services
from app.services.library.models import (
    ReservationCancel as LibReservationCancel,
    ReservationCreate as LibReservationCreate,
    ReservationResponse as LibReservationResponse,
)
from app.services.library.table import RESERVATION_STATUS_TEXT as LIB_STATUS_TEXT
from app.services.library.table import STATUS_NORMAL as LIB_STATUS_NORMAL

logger = loguru.logger.bind(name=__name__)

# 主 Agent 能看到的工具名（中间件的工具白名单直接引用它，避免两处各写一份）
RESERVATION_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "make_reservation",
        "list_my_reservations",
        "cancel_my_reservation",
        "search_labs",
        "list_lab_slots",
        "search_study_rooms",
        "find_available_seats",
    }
)

# **写操作**：会改数据库、而且学生自己不一定能撤销的那两个。
# 它们不会被执行，而是被中间件拦成「待确认」（见 app/harness/middleware.py 的
# write_confirmation_middleware 与 app/harness/confirm.py），确认后才由
# submit_reservation / cancel_reservation_for_user 真正落库。
WRITE_TOOL_NAMES: frozenset[str] = frozenset({"make_reservation", "cancel_my_reservation"})

__all__ = [
    "RESERVATION_TOOL_NAMES",
    "WRITE_TOOL_NAMES",
    "reservation_tools",
    # 执行层（图内部用）
    "prepare_lab_reservation",
    "execute_lab_reservation",
    "prepare_library_reservation",
    "execute_library_reservation",
    "parse_reserve_datetime",
    # 写操作的「提议 → 确认 → 执行」三段（工具与确认闸门共用）
    "describe_write_action",
    "propose_write_action",
    "submit_reservation",
    "cancel_reservation_for_user",
    "list_reservations_for_user",
    # 工具本体
    "make_reservation",
    "list_my_reservations",
    "cancel_my_reservation",
    "search_labs",
    "list_lab_slots",
    "search_study_rooms",
    "find_available_seats",
]


# ==================================================================
# 0. 通用小工具
# ==================================================================
@contextmanager
def _session() -> Iterator[Session]:
    """一次工具调用一个短事务；提交在业务服务里做，这里只负责开关连接"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 时间解析：模型给的可能是完整时间、ISO、也可能只有时分
_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
)
_TIME_FORMATS = ("%H:%M:%S", "%H:%M")
_DAY_WORDS = (("今天", 0), ("今日", 0), ("明天", 1), ("明日", 1), ("后天", 2))
# 只认「2026-10-16」这种日期，避免把时间当成日期
_DATE_RE = re.compile(r"^\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s*$")


def parse_reserve_date(value: str | None, *, now: datetime | None = None) -> date:
    """解析日期字符串（支持 2026-10-16 / 2026/10/16 / 今天 / 明天 / 后天）

    value 为空时返回今天（自习室/实验室的查询默认看当天）。
    """
    now = now or datetime.now()
    if value is None or not str(value).strip():
        return now.date()

    text = str(value).strip()
    for word, offset in _DAY_WORDS:
        if text == word or text.startswith(word):
            return now.date() + timedelta(days=offset)

    matched = _DATE_RE.match(text)
    if matched:
        year, month, day = (int(part) for part in matched.groups())
        return date(year, month, day)

    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"日期格式不对：{text}（请用 2026-10-16 或 今天/明天）")


def parse_reserve_datetime(
    value: str,
    *,
    default_date: date | None = None,
    now: datetime | None = None,
    field: str = "时间",
) -> datetime:
    """把「时间字符串」解析成本地 naive datetime

    支持：2026-10-16 14:00 / 2026-10-16T14:00:00 / 14:00 / 明天 14:00。
    只给时分时，日期取 default_date（预约日期），没给就取今天。
    """
    now = now or datetime.now()
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field}不能为空（请用 2026-10-16 14:00 或 14:00）")

    # 「明天 14:00」这种前缀
    prefix_day: date | None = None
    for word, offset in _DAY_WORDS:
        if text.startswith(word):
            prefix_day = now.date() + timedelta(days=offset)
            text = text[len(word) :].strip()
            break

    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    # 带时区的 ISO 时间：统一转成本地 naive（数据库 DateTime 列不带时区）
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed

    for fmt in _TIME_FORMATS:
        try:
            clock = datetime.strptime(text, fmt).time()
        except ValueError:
            continue
        target_day = prefix_day or default_date or now.date()
        return datetime.combine(target_day, clock)

    raise ValueError(f"{field}格式不对：{text}（请用 2026-10-16 14:00 或 14:00）")


def _period_text(start: Any, end: Any) -> str:
    """把起止时间排成人看的样子：10-16 14:00 ~ 16:00"""
    start_dt = start if isinstance(start, datetime) else parse_reserve_datetime(str(start))
    end_dt = end if isinstance(end, datetime) else parse_reserve_datetime(str(end))
    if start_dt.date() == end_dt.date():
        return f"{start_dt:%m-%d} {start_dt:%H:%M} ~ {end_dt:%H:%M}"
    return f"{start_dt:%m-%d %H:%M} ~ {end_dt:%m-%d %H:%M}"


def _validation_message(error: ValidationError) -> str:
    """把 pydantic 的校验错误压成一句人话"""
    parts: list[str] = []
    for item in error.errors():
        field = ".".join(str(loc) for loc in item.get("loc", ()) if loc != "body")
        message = str(item.get("msg", "")).removeprefix("Value error, ")
        parts.append(f"{field}: {message}" if field else message)
    return "；".join(parts) or "预约参数不合法"


def _identity(runtime: ToolRuntime[AgentContext] | None) -> tuple[int | None, str, str]:
    """从 AgentContext 取当前用户；返回 (user_id, user_name, user_role)

    没登录时 user_id 为 None，工具会回一句「请先登录」而不是抛异常
    （agent 拿到一句话还能继续对话）。
    """
    context = getattr(runtime, "context", None) if runtime is not None else None
    if isinstance(context, Mapping):
        return (
            context.get("user_id"),
            str(context.get("user_name") or ""),
            str(context.get("user_role") or "student"),
        )
    if context is None:
        return None, "", "student"
    return (
        getattr(context, "user_id", None),
        str(getattr(context, "user_name", "") or ""),
        str(getattr(context, "user_role", "student") or "student"),
    )


def _conflict_reason(error: Exception) -> str:
    """冲突类异常补充「被哪个时段/单号占了」，让模型能给出可操作的建议"""
    details_method = getattr(error, "conflict_details", None)
    if callable(details_method):
        details = details_method()
        if details:
            occupied = "、".join(
                f"{_period_text(item['start_time'], item['end_time'])}（{item['reservation_no']}）"
                for item in details[:3]
            )
            return f"{error.message}；已被占用：{occupied}"
    return getattr(error, "message", str(error))


# ==================================================================
# 1. 执行层：参数与图输入状态对齐（图节点调用，不直接给模型看）
# ==================================================================
def prepare_lab_reservation(
    state: Mapping[str, Any],
) -> tuple[LabReservationCreate | None, str | None]:
    """把图状态落成实验室预约请求模型；返回 (模型, 失败原因)"""
    lab_id = state.get("lab_id")
    if not lab_id:
        return None, "缺少 lab_id：请先调用 search_labs 查到要预约的实验室ID"

    try:
        default_date = parse_reserve_date(state.get("reserve_date"))
        data = LabReservationCreate(
            lab_id=int(lab_id),
            reserve_start_time=parse_reserve_datetime(
                state.get("reserve_start_time", ""),
                default_date=default_date,
                field="预约开始时间",
            ),
            reserve_end_time=parse_reserve_datetime(
                state.get("reserve_end_time", ""),
                default_date=default_date,
                field="预约结束时间",
            ),
            purpose=state.get("purpose") or None,
            people_num=state.get("people_num") or None,
        )
    except ValidationError as error:
        return None, _validation_message(error)
    except ValueError as error:
        return None, str(error)
    return data, None


def execute_lab_reservation(
    data: LabReservationCreate,
    *,
    user_id: int,
    user_name: str,
) -> dict[str, Any]:
    """执行实验室预约，返回 {ok, reservation, reason}

    失败也返回 dict 而不是抛异常：图节点要把原因写进状态，输出节点再变成一句人话。
    """
    try:
        with _session() as db:
            response: LabReservationResponse = lab_services.create_reservation(
                db, data, user_id=user_id, user_name=user_name
            )
    except lab_exc.ReservationTimeConflictError as error:
        return {"ok": False, "reason": _conflict_reason(error)}
    except lab_exc.LaboratoryException as error:
        return {"ok": False, "reason": error.message}
    except Exception:  # noqa: BLE001
        logger.exception("实验室预约失败：lab_id={}", data.lab_id)
        return {"ok": False, "reason": "系统暂时无法处理实验室预约，请稍后再试"}

    return {"ok": True, "reservation": response.model_dump(mode="json")}


def prepare_library_reservation(
    state: Mapping[str, Any],
) -> tuple[LibReservationCreate | None, str | None]:
    """把图状态落成自习室座位预约请求模型；返回 (模型, 失败原因)"""
    seat_id = state.get("seat_id")
    if not seat_id:
        return None, "缺少 seat_id：请先用 search_study_rooms / find_available_seats 查到座位ID"

    try:
        default_date = parse_reserve_date(state.get("reserve_date"))
        data = LibReservationCreate(
            seat_id=int(seat_id),
            reserve_start_time=parse_reserve_datetime(
                state.get("reserve_start_time", ""),
                default_date=default_date,
                field="预约开始时间",
            ),
            reserve_end_time=parse_reserve_datetime(
                state.get("reserve_end_time", ""),
                default_date=default_date,
                field="预约结束时间",
            ),
        )
    except ValidationError as error:
        return None, _validation_message(error)
    except ValueError as error:
        return None, str(error)
    return data, None


def execute_library_reservation(
    data: LibReservationCreate,
    *,
    user_id: int,
    user_name: str,
    auto_approve: bool = True,
) -> dict[str, Any]:
    """执行自习室座位预约

    auto_approve=True（默认，先到先得）提交即成功；
    False 则落成待审核，走管理员审核流程（与实验室一致）。
    """
    try:
        with _session() as db:
            response: LibReservationResponse = library_services.create_reservation(
                db,
                data,
                user_id=user_id,
                user_name=user_name,
                auto_approve=bool(auto_approve),
            )
    except lib_exc.ReservationTimeConflictError as error:
        return {"ok": False, "reason": _conflict_reason(error)}
    except lib_exc.ReservationUserConflictError as error:
        return {"ok": False, "reason": error.message}
    except lib_exc.LibraryException as error:
        return {"ok": False, "reason": error.message}
    except Exception:  # noqa: BLE001
        logger.exception("自习室座位预约失败：seat_id={}", data.seat_id)
        return {"ok": False, "reason": "系统暂时无法处理座位预约，请稍后再试"}

    return {"ok": True, "reservation": response.model_dump(mode="json")}


# ==================================================================
# 1.5 写操作的「提议 → 确认 → 执行」：工具与确认闸门共用
#
# 为什么要拆成三个函数而不是留在工具里：
#
#     propose_write_action   在校验参数、生成一句人话（工具被拦成「待确认」时用）
#     submit_reservation     真正落库，**身份是显式参数**，不需要 ToolRuntime
#
# 确认闸门（app/harness/confirm.py）在 HTTP 请求里被调用，那里没有 LangChain 的
# ToolRuntime（`runtime: ToolRuntime[AgentContext]` 是框架注入的），所以执行逻辑不能
# 写在 `@tool` 函数体里 —— 拆出来之后，CLI / FastAPI / 未来别的入口都能直接调。
# ==================================================================
def submit_reservation(
    *,
    user_id: int,
    user_name: str,
    user_role: str = "student",
    affair_name: str,
    reserve_start_time: str,
    reserve_end_time: str,
    lab_id: int | None = None,
    seat_id: int | None = None,
    reserve_date: str | None = None,
    people_num: int | None = None,
    purpose: str | None = None,
    auto_approve: bool = True,
) -> str:
    """执行一次预约（**确认之后**才调用它）

    身份全部走参数（由工具从 AgentContext / 由确认闸门从登录态取），模型既填不了
    也改不了；参数校验、下单、错误翻译都在预约图里，这里只负责把状态拼好。
    """
    if affair_name not in ("lab", "library"):
        return f"暂不支持的预约类型 {affair_name!r}，目前只能预约实验室(lab)或图书馆自习室(library)"

    # 延迟导入：app.tool 与 app.harness.graph 互相引用，模块顶层导入会循环
    from app.harness.graph import reservation_graph

    state: dict[str, Any] = {
        "affair_name": affair_name,
        "user_id": int(user_id),
        "user_name": str(user_name or ""),
        "user_role": str(user_role or "student"),
        "reserve_start_time": reserve_start_time,
        "reserve_end_time": reserve_end_time,
        "lab_id": lab_id,
        "seat_id": seat_id,
        "reserve_date": reserve_date,
        "people_num": people_num,
        "purpose": purpose,
        "auto_approve": bool(auto_approve),
    }
    try:
        result = reservation_graph.invoke(state)
    except Exception:  # noqa: BLE001
        logger.exception("预约图执行失败：{}", affair_name)
        return "预约流程出错，请稍后再试。"
    return result.get("output") or "预约没有完成，请稍后再试。"


def cancel_reservation_for_user(
    *,
    user_id: int,
    user_role: str = "student",
    affair_name: str,
    reservation_id: int,
    reason: str | None = None,
) -> str:
    """取消**某个学生自己的**预约（确认之后才调用；业务层会再校验一次归属）"""
    if affair_name not in ("lab", "library"):
        return f"暂不支持的预约类型 {affair_name!r}"

    try:
        with _session() as db:
            if affair_name == "lab":
                result = lab_services.cancel_reservation(
                    db,
                    LabReservationCancel(id=reservation_id, remark=reason),
                    user_id=user_id,
                    user_role=user_role,
                )
                target = f"实验室「{result.lab_name}」"
                period = f"{result.reserve_start_time:%m-%d %H:%M}-{result.reserve_end_time:%H:%M}"
            else:
                result = library_services.cancel_reservation(
                    db,
                    LibReservationCancel(id=reservation_id, remark=reason),
                    user_id=user_id,
                    user_role=user_role,
                )
                target = f"自习室「{result.room_name}」{result.seat_code} 号座"
                period = f"{result.reserve_start_time:%m-%d %H:%M}-{result.reserve_end_time:%H:%M}"
    except lab_exc.LaboratoryException as error:
        return f"取消失败：{error.message}"
    except lib_exc.LibraryException as error:
        return f"取消失败：{error.message}"
    except Exception:  # noqa: BLE001
        logger.exception("取消预约失败：{}", reservation_id)
        return "取消预约失败，请稍后再试。"

    return f"已取消预约 {result.reservation_no}｜{target}｜{period}，该时段已释放。"


def list_reservations_for_user(
    *,
    user_id: int,
    affair_name: str = "all",
    status: int | None = None,
) -> str:
    """查询某个学生自己的预约记录（读操作，不需要确认）"""
    blocks: list[str] = []
    try:
        with _session() as db:
            if affair_name in ("all", "lab"):
                page = lab_services.get_user_reservation_page(
                    db, user_id, page=1, page_size=20, status=status
                )
                blocks.append(_format_lab_reservations(page.data, "实验室预约"))
            if affair_name in ("all", "library"):
                page = library_services.get_user_reservation_page(
                    db, user_id, page=1, page_size=20, status=status
                )
                blocks.append(_format_lib_reservations(page.data, "自习室座位预约"))
    except Exception:  # noqa: BLE001
        logger.exception("查询我的预约失败")
        return "查询预约记录失败，请稍后再试。"

    text = "\n\n".join(block for block in blocks if block)
    return text or "当前没有符合条件的预约记录。"


def describe_write_action(tool_name: str, args: Mapping[str, Any]) -> str:
    """把一次写操作翻译成一句人话（给模型复述、给确认接口返回、给日志）"""
    args = dict(args or {})

    if tool_name == "make_reservation":
        affair = str(args.get("affair_name") or "")
        if affair == "lab":
            target = f"实验室（lab_id={args.get('lab_id')}）"
        elif affair == "library":
            target = f"自习室座位（seat_id={args.get('seat_id')}）"
        else:
            target = f"未知类型 {affair!r}"
        when = f"{args.get('reserve_start_time') or '?'} ~ {args.get('reserve_end_time') or '?'}"
        if args.get("reserve_date"):
            when += f"（日期：{args['reserve_date']}）"
        extras: list[str] = []
        if args.get("people_num"):
            extras.append(f"人数 {args['people_num']}")
        if args.get("purpose"):
            extras.append(f"用途 {args['purpose']}")
        if affair == "library" and args.get("auto_approve") is False:
            extras.append("提交后需管理员审核")
        tail = "，" + "，".join(extras) if extras else ""
        return f"预约{target}，时间 {when}{tail}"

    if tool_name == "cancel_my_reservation":
        affair = "实验室预约" if args.get("affair_name") == "lab" else "自习室座位预约"
        text = f"取消{affair}（预约ID {args.get('reservation_id')}）"
        return text + (f"，原因：{args['reason']}" if args.get("reason") else "")

    return f"执行写操作 {tool_name}"


def propose_write_action(tool_name: str, args: Mapping[str, Any]) -> tuple[bool, str]:
    """写操作提交前的**静态校验** + 一句话摘要；返回 (是否可提交, 摘要或原因)

    在「问学生要不要确认」之前先把参数校验掉：时间写错、缺 lab_id 这种问题
    现在就能让模型改参数，而不是让学生确认一个注定失败的预约、再看到失败原因。
    """
    args = dict(args or {})

    if tool_name == "make_reservation":
        affair = str(args.get("affair_name") or "")
        if affair == "lab":
            data, reason = prepare_lab_reservation(args)
        elif affair == "library":
            data, reason = prepare_library_reservation(args)
        else:
            return False, "affair_name 只能是 lab 或 library"
        if data is None:
            return False, reason or "预约参数不完整"
        return True, describe_write_action(tool_name, args)

    if tool_name == "cancel_my_reservation":
        if args.get("affair_name") not in ("lab", "library"):
            return False, "affair_name 只能是 lab 或 library"
        try:
            reservation_id = int(args.get("reservation_id"))
        except (TypeError, ValueError):
            return False, "reservation_id 必须是整数（先用 list_my_reservations 查出来）"
        if reservation_id <= 0:
            return False, "reservation_id 必须是正整数（先用 list_my_reservations 查出来）"
        return True, describe_write_action(
            tool_name, {**args, "reservation_id": reservation_id}
        )

    return False, f"{tool_name} 不是写操作"


# ==================================================================
# 2. 查询层：帮主 Agent 把 lab_id / seat_id 查出来
# ==================================================================
@tool
def search_labs(keyword: str | None = None, people_num: int | None = None) -> str:
    """查询当前**开放中**的实验室，返回 lab_id 与实验室基本信息。

    预约实验室必须先有 lab_id，这是唯一能拿到 lab_id 的入口，不要自己编造。

    什么时候用：学生说「想预约实验室 / 找个能做实验的地方 / 还有没有空实验室」时，
    先用它把候选实验室列出来。拿到 lab_id 之后的正常顺序是：
    list_lab_slots 看某天还剩哪些时段 → make_reservation 提交预约。

    Args:
        keyword (str | None): 关键词，模糊匹配实验室名称或位置，如「物理」「A101」。可不传。
        people_num (int | None): 预计使用人数，如 10。传了只会返回容量足够的实验室。可不传。

    Returns:
        首行是符合条件的实验室数量，之后每行一个实验室，依次为
        lab_id、名称、位置、容量上限、开放时间。没有符合条件的实验室时返回一句说明，
        此时应建议学生放宽关键词或人数条件，或改看图书馆自习室，不要假设某个实验室存在。
    """
    try:
        with _session() as db:
            labs = lab_services.list_labs(
                db,
                keyword=keyword,
                people_num=people_num,
                status=LAB_STATUS_NORMAL,
            )
    except Exception:  # noqa: BLE001
        logger.exception("查询实验室失败")
        return "查询实验室失败，请稍后再试或让学生直接联系实验室管理员。"

    if not labs:
        return "没有找到符合条件的开放实验室。可以放宽关键词或人数条件再试试，也可以看看图书馆自习室。"

    lines = [f"当前开放实验室 {len(labs)} 个（lab_id / 名称 / 位置 / 容量 / 开放时间）："]
    for lab in labs:
        window = (
            f"{lab.open_time:%H:%M}-{lab.close_time:%H:%M}"
            if lab.open_time and lab.close_time
            else "全天"
        )
        lines.append(
            f"- lab_id={lab.id}｜{lab.name}｜{lab.location or '位置未填'}"
            f"｜最多 {lab.maxpeople} 人｜开放 {window}"
        )
    return "\n".join(lines)


@tool
def list_lab_slots(lab_id: int, day: str | None = None) -> str:
    """查询某个实验室某一天的占用情况与可预约时段（按整点切分）。

    什么时候用：学生已经挑中实验室（有 lab_id）但不确定哪天哪个时段有空时；
    或学生说出了具体时段、需要先确认会不会和别人冲突时。

    Args:
        lab_id (int): search_labs 返回的实验室ID，不要凭猜测填写。
        day (str | None): 日期，支持「2026-10-16」「今天」「明天」「后天」，
            不传默认今天。

    Returns:
        几行文本：实验室名称与该日期的开放时间；当天已有预约（含待审核）的时段、
        状态与预约人；以及按整点切分的可预约时段（✅ 空闲 / ❌ 已占用）。
        可预约时段为「无」时，如实说明当天约不上，建议换个日期或实验室。
        注意：实验室预约提交后是**待审核**状态，管理员通过后才算约上。
    """
    try:
        day_date = parse_reserve_date(day)
        with _session() as db:
            lab = lab_services.get_lab_detail(db, lab_id)
            schedule = lab_services.get_lab_schedule(db, lab_id, day_date)
            slots = lab_services.list_available_slots(db, lab_id, day_date)
    except lab_exc.LabNotFoundError:
        return f"实验室 {lab_id} 不存在，请先用 search_labs 查一下可用的实验室。"
    except lab_exc.LabClosedError:
        return f"实验室 {lab_id} 当前已关闭，暂时不能预约。"
    except ValueError as error:
        return f"{error}"
    except Exception:  # noqa: BLE001
        logger.exception("查询实验室时段失败：lab_id={}", lab_id)
        return "查询实验室时段失败，请稍后再试。"

    window = (
        f"{lab.open_time:%H:%M}-{lab.close_time:%H:%M}"
        if lab.open_time and lab.close_time
        else "全天开放"
    )
    lines = [f"{lab.name}（lab_id={lab_id}）{day_date:%Y-%m-%d}｜开放 {window}"]

    if schedule:
        lines.append(f"当天已有预约 {len(schedule)} 条（含待审核）：")
        for item in schedule:
            lines.append(
                f"- {item.reserve_start_time:%H:%M}-{item.reserve_end_time:%H:%M}"
                f"｜{LAB_STATUS_TEXT.get(item.status, item.status)}｜{item.user_name}"
            )
    else:
        lines.append("当天还没有任何预约。")

    if not slots:
        lines.append("可预约时段：无（可能已过开放时间或已过当天可约时间）")
    else:
        available = [slot for slot in slots if slot.available]
        lines.append(
            f"可预约时段（{len(available)}/{len(slots)} 个空闲，每人最多提前 7 天、当天内预约）："
        )
        lines.append(
            "  ".join(
                f"{slot.start_time:%H:%M}-{slot.end_time:%H:%M}{'✅' if slot.available else '❌'}"
                for slot in slots
            )
        )
    return "\n".join(lines)


@tool
def search_study_rooms(keyword: str | None = None) -> str:
    """查询当前**开放中**的图书馆自习室，返回 room_id 与自习室基本信息。

    预约座位必须先有 room_id，这是唯一能拿到 room_id 的入口，不要自己编造。

    什么时候用：学生说「想自习 / 找个座位 / 图书馆还有没有位置」时，
    先用它选出自习室，再用 find_available_seats 看某个时段哪些座位空着，
    最后用 make_reservation 提交预约。

    Args:
        keyword (str | None): 关键词，模糊匹配自习室名称或位置，如「三楼」「静音」。可不传。

    Returns:
        首行是符合条件的自习室数量，之后每行一个自习室，依次为
        room_id、名称、位置、座位数上限、开放时间。没有符合条件的自习室时返回一句说明，
        此时如实告诉学生即可，不要假设某个自习室存在。
    """
    try:
        with _session() as db:
            rooms = library_services.list_rooms(
                db, keyword=keyword, status=LIB_STATUS_NORMAL
            )
    except Exception:  # noqa: BLE001
        logger.exception("查询自习室失败")
        return "查询自习室失败，请稍后再试。"

    if not rooms:
        return "没有找到符合条件的开放自习室。"

    lines = [f"当前开放自习室 {len(rooms)} 个（room_id / 名称 / 位置 / 座位数上限 / 开放时间）："]
    for room in rooms:
        window = (
            f"{room.open_time:%H:%M}-{room.close_time:%H:%M}"
            if room.open_time and room.close_time
            else "全天"
        )
        lines.append(
            f"- room_id={room.id}｜{room.name}｜{room.location or '位置未填'}"
            f"｜最多 {room.capacity} 个座位｜开放 {window}"
        )
    return "\n".join(lines)


@tool
def find_available_seats(
    room_id: int,
    start_time: str,
    end_time: str,
    reserve_date: str | None = None,
) -> str:
    """查询某个自习室在指定时间段还有哪些**空座位**，返回 seat_id。

    预约座位必须落到具体座位上，seat_id 只能从这里拿，不要自己编造。

    什么时候用：学生已经选好自习室（有 room_id）、说清大概几点到几点自习时，
    用它看这个时段有哪些座位空着，挑一个再提交预约。

    Args:
        room_id (int): search_study_rooms 返回的自习室ID。
        start_time (str): 开始时间，如「14:00」或「2026-10-16 14:00」。
        end_time (str): 结束时间，格式同上。
        reserve_date (str | None): 日期，支持「2026-10-16」「今天」「明天」；
            start_time / end_time 里已经带日期时可不传。

    Returns:
        首行是自习室名称、查询时段和空座位数（空 / 总），之后每行一个空座位，依次为
        seat_id、座位编号、区域、是否有电源；空座位较多时只列出前 30 个。
        没有空座位时返回一句说明，此时建议换个时段或换一间自习室。
        注意：自习室单次预约最长 4 小时、最多提前 3 天，且必须在开放时间内。
    """
    try:
        default_date = parse_reserve_date(reserve_date)
        start_dt = parse_reserve_datetime(
            start_time, default_date=default_date, field="开始时间"
        )
        end_dt = parse_reserve_datetime(
            end_time, default_date=default_date, field="结束时间"
        )
        with _session() as db:
            room = library_services.get_room_detail(db, room_id)
            free_seats = library_services.list_available_seats(
                db, room_id, start_dt, end_dt
            )
            all_seats = library_services.list_seats(db, room_id)
    except ValueError as error:
        return f"{error}"
    except lib_exc.RoomNotFoundError:
        return f"自习室 {room_id} 不存在，请先用 search_study_rooms 查一下。"
    except lib_exc.RoomClosedError:
        return f"自习室 {room_id} 当前已关闭，暂时不能预约。"
    except lib_exc.LibraryException as error:
        return error.message
    except Exception:  # noqa: BLE001
        logger.exception("查询空座位失败：room_id={}", room_id)
        return "查询空座位失败，请稍后再试。"

    period = f"{_period_text(start_dt, end_dt)}"
    if not free_seats:
        return (
            f"{room.name} 在 {period} 已经没有空座位了"
            f"（共 {len(all_seats)} 个座位）。可以换个时段，或换一间自习室。"
        )

    lines = [
        f"{room.name} {period} 有 {len(free_seats)}/{len(all_seats)} 个空座位："
    ]
    for seat in free_seats[:30]:
        tags = []
        if seat.area:
            tags.append(seat.area)
        tags.append("有电源" if seat.has_power else "无电源")
        lines.append(f"- seat_id={seat.id}｜{seat.code}｜{'，'.join(tags)}")
    if len(free_seats) > 30:
        lines.append(f"（还有 {len(free_seats) - 30} 个座位未列出）")
    return "\n".join(lines)


# ==================================================================
# 3. 预约入口：主 Agent 唯一的预约工具（内部走 app/harness/graph.py 的预约图）
# ==================================================================
@tool
def make_reservation(
    runtime: ToolRuntime[AgentContext],
    affair_name: Literal["lab", "library"],
    reserve_start_time: str,
    reserve_end_time: str,
    lab_id: int | None = None,
    seat_id: int | None = None,
    reserve_date: str | None = None,
    people_num: int | None = None,
    purpose: str | None = None,
    auto_approve: bool = True,
) -> str:
    """提交一次预约：实验室（lab）或图书馆自习室座位（library）。这是唯一的预约入口。

    **写操作**：这个工具本身不会直接下单，调用后会返回「待确认」，由学生本人确认后
    才真正提交（见 app/harness/confirm.py）。返回「待确认」时，请把待预约的对象和时段
    复述给学生并询问是否确认，**不要**说已经约好了。

    **提交前必须先查 id**：实验室先用 search_labs 拿 lab_id，
    自习室先用 search_study_rooms + find_available_seats 拿 seat_id，不要自己编造 id。

    什么时候用：学生明确要预约实验室或自习室座位，并且对象和时段都已经确认清楚时。

    必须遵守：
    1. 预约人身份由系统自动带入，**不要向学生索要用户ID，也不要代别人预约**。
    2. 时间必须同一天，不能约过去，最多提前 7 天（实验室）/ 3 天（自习室）。
    3. 自习室单次最长 4 小时，一人同一时段只能占一个座位。
    4. 只有「预约成功」的记录才真正占座；冲突时工具会返回被占用的时段，
       据此换时段或换对象，不要把同一时段重复提交。

    Args:
        affair_name (Literal["lab", "library"]): "lab" 预约实验室（必须给 lab_id）；
            "library" 预约自习室座位（必须给 seat_id）。
        reserve_start_time (str): 开始时间，如「14:00」「2026-10-16 14:00」「明天 14:00」。
        reserve_end_time (str): 结束时间，格式同上。
        lab_id (int | None): 实验室ID，affair_name=lab 时必填。
        seat_id (int | None): 座位ID，affair_name=library 时必填。
        reserve_date (str | None): 日期，支持「2026-10-16」「今天」「明天」；
            开始/结束时间已带日期时可不传。
        people_num (int | None): 实验室使用人数，不能超过实验室容量；仅 lab 有效，可不传。
        purpose (str | None): 实验室使用目的或实验内容；仅 lab 有效，可不传。
        auto_approve (bool): 仅对 library 有效。True = 先到先得、提交即成功（默认）；
            False = 提交后等管理员审核。

    Returns:
        一句话的预约结果、「待确认」说明，或失败原因（如时间冲突、容量不足、未登录）。
        请据此回复学生，**不要自己编造预约单号，也不要说「预约成功」**。
    """
    user_id, user_name, user_role = _identity(runtime)
    if user_id is None:
        return "还没有登录，无法替你预约。请学生先登录校园系统再预约。"

    return submit_reservation(
        user_id=int(user_id),
        user_name=user_name,
        user_role=user_role,
        affair_name=affair_name,
        reserve_start_time=reserve_start_time,
        reserve_end_time=reserve_end_time,
        lab_id=lab_id,
        seat_id=seat_id,
        reserve_date=reserve_date,
        people_num=people_num,
        purpose=purpose,
        auto_approve=auto_approve,
    )


@tool
def list_my_reservations(
    runtime: ToolRuntime[AgentContext],
    affair_name: Literal["all", "lab", "library"] = "all",
    status: int | None = None,
) -> str:
    """查询**当前登录学生自己的**预约记录，默认同时查实验室和自习室。

    什么时候用：学生问「我约了什么 / 我的预约还在不在 / 我预约几点了」时；
    以及取消预约之前必须先查到 reservation_id 时。

    注意：身份由系统自动带入，查到的永远是当前学生自己的记录，
    不要用它查别人的预约。

    Args:
        affair_name (Literal["all", "lab", "library"]): "all"（默认）两者都查；
            "lab" 只看实验室；"library" 只看自习室。
        status (int | None): 预约状态筛选，0-待审核 / 1-预约成功 / 2-预约失败 / 3-已取消；
            不传查全部。

    Returns:
        一份按记录分组的清单，每行含 id、预约单号、预约对象、时段、状态，
        以及备注（如果有）。没有记录时返回「暂无记录」，此时如实告诉学生即可。
    """
    user_id, _, _ = _identity(runtime)
    if user_id is None:
        return "还没有登录，查不到预约记录。请学生先登录校园系统。"

    return list_reservations_for_user(
        user_id=int(user_id), affair_name=affair_name, status=status
    )


@tool
def cancel_my_reservation(
    runtime: ToolRuntime[AgentContext],
    affair_name: Literal["lab", "library"],
    reservation_id: int,
    reason: str | None = None,
) -> str:
    """取消**当前登录学生自己的**预约（待审核 / 预约成功都可以取消，取消后自动释放时段）。

    **写操作**：这个工具不会直接取消，调用后会返回「待确认」，学生本人确认之后
    才真正执行。返回「待确认」时，请把待取消的记录复述给学生并询问是否确认，
    **不要**说已经取消了。

    **取消前先查 id**：先用 list_my_reservations 查到 reservation_id，
    不要凭猜测的 id 取消；确认不了就先查再取消。

    什么时候用：学生明确说「不去了 / 帮我取消预约 / 那个时段用不上了」时。

    Args:
        affair_name (Literal["lab", "library"]): "lab" 取消实验室预约；
            "library" 取消自习室座位预约。
        reservation_id (int): 预约ID，取自 list_my_reservations 返回的 id。
        reason (str | None): 取消原因，可不传。

    Returns:
        一句话的取消结果、「待确认」说明，或失败原因（如记录不存在、已取消、未登录）。
        请据此回复学生，**不要自己编造已取消的结论**。
    """
    user_id, _, user_role = _identity(runtime)
    if user_id is None:
        return "还没有登录，无法取消预约。请学生先登录校园系统。"

    return cancel_reservation_for_user(
        user_id=int(user_id),
        user_role=user_role,
        affair_name=affair_name,
        reservation_id=reservation_id,
        reason=reason,
    )


# ==================================================================
# 4. 结果排版（list_my_reservations 用）
# ==================================================================
def _format_lab_reservations(items: list[LabReservationResponse], title: str) -> str:
    if not items:
        return f"{title}：暂无记录。"
    lines = [f"{title}（{len(items)} 条）："]
    for item in items:
        lines.append(
            f"- id={item.id}｜{item.reservation_no}｜{item.lab_name}"
            f"｜{_period_text(item.reserve_start_time, item.reserve_end_time)}"
            f"｜{LAB_STATUS_TEXT.get(item.status, item.status)}"
            + (f"｜说明：{item.remark}" if item.remark else "")
        )
    return "\n".join(lines)


def _format_lib_reservations(items: list[LibReservationResponse], title: str) -> str:
    if not items:
        return f"{title}：暂无记录。"
    lines = [f"{title}（{len(items)} 条）："]
    for item in items:
        lines.append(
            f"- id={item.id}｜{item.reservation_no}｜{item.room_name} {item.seat_code}"
            f"｜{_period_text(item.reserve_start_time, item.reserve_end_time)}"
            f"｜{LIB_STATUS_TEXT.get(item.status, item.status)}"
            + (f"｜说明：{item.remark}" if item.remark else "")
        )
    return "\n".join(lines)


# 主 Agent 的预约相关工具（app/tool/__init__.py 会把它拼进 user_tool）
reservation_tools = [
    make_reservation,
    search_labs,
    list_lab_slots,
    search_study_rooms,
    find_available_seats,
    list_my_reservations,
    cancel_my_reservation,
]
