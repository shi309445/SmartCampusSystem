"""预约子图：实验室预约 + 图书馆自习室座位预约

**它解决什么问题**

主 Agent 需要「预约」这一项能力，而后端是两套业务（app/services/laboratory 的实验
室预约、app/services/library 的自习室座位预约）：入参不同、校验不同、失败原因不同。
如果让主 Agent 直接对两个服务各写一个工具，它得先判断该用哪个、再记两套参数和两套
错误码。这里把两条流水线收敛成**一张图**，对外只暴露一个入口
（app/tool/reservation_tool.py 的 make_reservation），主 Agent 只要说清
「约什么、什么时候、约哪个对象」就行：

                ┌─ affair_name == "lab" ──────→ lab_pretreatment ──→ lab_dispose ─────┐
    START ──────┤                                                                      ├──→ output_node ──→ END
                └─ affair_name == "library" ──→ library_pretreatment → library_dispose ┘
                                                          （不认识的类型 → unsupported_node）

每个分支都是固定的两段，职责切得很干净：

    pretreatment  把状态里的原始参数（字符串时间、可能缺字段）落成**请求模型**
                  （pydantic 校验 + 时间解析），失败原因写进 state["reason"]
    dispose       调业务服务真正下单，把结果（含预约单号）转成可序列化的 dict
    output_node   把 state 变成一句给学生的中文结果，模型只负责转述

**为什么用图而不是两个 if**

    1. 三个阶段的代码各自独立：加新业务（比如体育场馆）只需要加一组
       {x}_pretreatment / {x}_dispose 节点和一个分支，不动其他业务；
    2. 状态机天然可观测：哪一步失败、失败原因是哪句，都在 state 里；
    3. 将来接 checkpointer 就能中断/恢复（比如「等我确认再提交」的人工确认节点）。

**状态设计的三条硬约束**

    1. **不把 Session 放进状态**。状态要能被序列化（checkpointer / 回放 / 前端流式都依赖它），
       数据库会话由工具层自己开关（一次工具调用 = 一个短事务）。
    2. **输入状态与工具参数严格对齐**：ReservationInput 的每个字段都对应
       app/tool/reservation_tool.py 里 make_reservation 的同名参数，
       主 Agent 把工具参数原样丢进来即可，中间不做二次映射。
    3. **身份只信工具注入**：user_id / user_name / user_role 由工具从 AgentContext 取，
       模型既填不了也改不了（防止伪造申请人、代别人预约）。

**失败不重试**：预约失败（时段冲突、不在开放时间、超容量……）重复执行同一个节点不会
变成成功，所以 dispose 之后一律进输出节点，把原因回给模型，由它决定换时段还是换对象。
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph

from app.services.laboratory.models import ReservationCreate as LabReservationCreate
from app.services.laboratory.table import RESERVATION_STATUS_TEXT as LAB_STATUS_TEXT
from app.services.library.models import ReservationCreate as LibraryReservationCreate
from app.services.library.table import RESERVATION_STATUS_TEXT as LIB_STATUS_TEXT
from app.tool.reservation_tool import (
    execute_lab_reservation,
    execute_library_reservation,
    prepare_lab_reservation,
    prepare_library_reservation,
)

__all__ = [
    "ReservationInput",
    "ReservationState",
    "ReservationOutput",
    "AffairName",
    "lab_pretreatment",
    "lab_dispose",
    "library_pretreatment",
    "library_dispose",
    "unsupported_node",
    "output_node",
    "route_affair",
    "format_reservation_result",
    "build_reservation_graph",
    "reservation_graph",
    "graph",
]

# 只支持这两类预约业务；新增业务时在这里加名字，并在 build_reservation_graph 里加分支
AffairName = Literal["lab", "library"]

# 状态里出现的各种「预约类型」写法，统一收敛到 lab / library
# （工具走的是 Literal 校验，这里主要是给直接 invoke 图的老调用方留一层容错）
_AFFAIR_ALIASES: dict[str, AffairName] = {
    "lab": "lab",
    "laboratory": "lab",
    "实验室": "lab",
    "library": "library",
    "room": "library",
    "seat": "library",
    "自习室": "library",
    "图书馆": "library",
}


# ==================================================================
# 状态定义
# ==================================================================
class ReservationInput(TypedDict):
    """图的输入状态 —— 字段与 make_reservation 的同名参数一一对齐"""

    # 分流：约实验室还是约自习室座位
    affair_name: AffairName
    # 身份：由工具从 AgentContext 注入，模型填不了
    user_id: int
    user_name: str
    # 时间：字符串（2026-10-16 14:00 / 14:00 / 明天 14:00），由 pretreatment 解析
    reserve_start_time: str
    reserve_end_time: str

    user_role: NotRequired[str]
    # 只给时分时用它补日期（2026-10-16 / 今天 / 明天 / 后天）
    reserve_date: NotRequired[str | None]
    # 实验室参数
    lab_id: NotRequired[int | None]
    people_num: NotRequired[int | None]
    purpose: NotRequired[str | None]
    # 自习室参数
    seat_id: NotRequired[int | None]
    auto_approve: NotRequired[bool]


class ReservationState(ReservationInput):
    """图运行时状态：在输入状态上追加「中间产物 / 执行结果」"""

    affair_data: NotRequired[LabReservationCreate | LibraryReservationCreate | None]
    treatment_state: NotRequired[bool]
    reason: NotRequired[str | None]
    reservation: NotRequired[dict[str, Any] | None]


class ReservationOutput(TypedDict):
    """图的输出状态：只对外暴露这几项"""

    treatment_state: bool
    reason: str | None
    reservation: dict[str, Any] | None
    output: str


# ==================================================================
# 节点
# ==================================================================
def _normalize_affair(value: Any) -> AffairName | None:
    """把 affair_name 归一化；不认识的返回 None（交给 unsupported_node）"""
    if value is None:
        return None
    text = str(value).strip().lower()
    return _AFFAIR_ALIASES.get(text)


def _fail(reason: str) -> dict[str, Any]:
    """统一的失败状态：后面 output_node 只认 treatment_state/reason"""
    return {"treatment_state": False, "reason": reason, "reservation": None}


def lab_pretreatment(state: ReservationState) -> dict[str, Any]:
    """实验室预约：把原始参数校验成 LabReservationCreate

    校验不过（缺 lab_id、时间格式不对、结束早于开始……）就直接把原因写进状态，
    dispose 看到 affair_data 为空会原样跳过，最终由 output_node 变成一句人话。
    """
    data, reason = prepare_lab_reservation(state)
    if data is None:
        return _fail(reason or "实验室预约参数不完整")
    return {"affair_data": data, "treatment_state": False, "reason": None}


def lab_dispose(state: ReservationState) -> dict[str, Any]:
    """实验室预约：真正下单（新记录默认「待审核」，与实验室业务流程一致）"""
    data = state.get("affair_data")
    if not isinstance(data, LabReservationCreate):
        return {}  # pretreatment 已经失败，原因在 state["reason"] 里

    result = execute_lab_reservation(
        data,
        user_id=int(state["user_id"]),
        user_name=str(state.get("user_name") or ""),
    )
    return {
        "treatment_state": bool(result.get("ok")),
        "reason": result.get("reason"),
        "reservation": result.get("reservation"),
    }


def library_pretreatment(state: ReservationState) -> dict[str, Any]:
    """自习室预约：把原始参数校验成 LibraryReservationCreate"""
    data, reason = prepare_library_reservation(state)
    if data is None:
        return _fail(reason or "自习室预约参数不完整")
    return {"affair_data": data, "treatment_state": False, "reason": None}


def library_dispose(state: ReservationState) -> dict[str, Any]:
    """自习室预约：真正下单

    auto_approve 默认 True（先到先得，提交即成功）；传 False 则变成待审核，
    与实验室走同一套管理员审核流程。
    """
    data = state.get("affair_data")
    if not isinstance(data, LibraryReservationCreate):
        return {}

    result = execute_library_reservation(
        data,
        user_id=int(state["user_id"]),
        user_name=str(state.get("user_name") or ""),
        auto_approve=bool(state.get("auto_approve", True)),
    )
    return {
        "treatment_state": bool(result.get("ok")),
        "reason": result.get("reason"),
        "reservation": result.get("reservation"),
    }


def unsupported_node(state: ReservationState) -> dict[str, Any]:
    """affair_name 不是 lab/library 时的兜底：给出明确原因，而不是静默走错分支"""
    return _fail(
        f"暂不支持的预约类型 {state.get('affair_name')!r}，目前只能预约实验室(lab)"
        "或图书馆自习室(library)"
    )


def output_node(state: ReservationState) -> dict[str, Any]:
    """把状态排成一句给学生的结果（主 Agent 直接转述这句话）"""
    return {"output": format_reservation_result(state)}


# ==================================================================
# 路由
# ==================================================================
def route_affair(state: ReservationState) -> str:
    """按 affair_name 分流；不认识的走 unsupported"""
    return _normalize_affair(state.get("affair_name")) or "unsupported"


# ==================================================================
# 输出排版
# ==================================================================
def _clock(value: Any) -> str:
    """datetime / ISO 字符串 -> 10-16 14:00（排版用，解析失败就原样返回）"""
    if isinstance(value, str):
        text = value.replace("T", " ")
        return text[5:16] if len(text) >= 16 else text
    try:
        return f"{value:%m-%d %H:%M}"
    except (TypeError, ValueError):  # pragma: no cover - 兜底
        return str(value)


def format_reservation_result(state: ReservationState) -> str:
    """预约结果 -> 一句中文

    成功/待审核/失败三种口径分开写，避免模型把「已提交待审核」说成「预约成功」。
    """
    affair = _normalize_affair(state.get("affair_name"))
    if not state.get("treatment_state"):
        reason = state.get("reason") or "预约没有完成"
        return f"预约没有成功：{reason}"

    data = state.get("reservation") or {}
    status = data.get("status")
    period = f"{_clock(data.get('reserve_start_time'))} ~ {_clock(data.get('reserve_end_time'))}"
    number = data.get("reservation_no", "")

    if affair == "lab":
        status_text = LAB_STATUS_TEXT
        target = f"实验室「{data.get('lab_name', '')}」"
        extra = ""
        if data.get("people_num"):
            extra += f"｜使用人数 {data['people_num']}"
        if data.get("purpose"):
            extra += f"｜用途：{data['purpose']}"
        tail = "请按时到实验室，凭预约单号使用。"
    else:
        status_text = LIB_STATUS_TEXT
        target = f"自习室「{data.get('room_name', '')}」{data.get('seat_code', '')} 号座"
        extra = ""
        tail = "请按时到馆就座；如果来不了，请提前取消，把座位释放给其他同学。"

    state_text = status_text.get(status, str(status))
    base = f"预约单号 {number}｜{target}｜{period}｜状态：{state_text}{extra}"

    if status == 1:
        return f"预约成功。{base}。{tail}"
    if status == 0:
        return f"已提交预约申请，正在等待管理员审核。{base}。审核通过后即可使用。"
    # 2-预约失败 / 3-已取消：正常流程不会走到这里（失败已在 execute 层转成 reason）
    return f"预约已记录（{state_text}）。{base}。"


# ==================================================================
# 组装
# ==================================================================
def build_reservation_graph():
    """建图：START → (lab | library | unsupported) → output_node → END

    新增一类预约业务时，只要：
        1. 在 AffairName / _AFFAIR_ALIASES 里加名字；
        2. 写两个节点 {x}_pretreatment / {x}_dispose；
        3. 在下面加 add_node + add_edge，并在 route_affair 的 path_map 里加分支。
    """
    builder = StateGraph(
        state_schema=ReservationState,
        input_schema=ReservationInput,
        output_schema=ReservationOutput,
    )

    builder.add_node("lab_pretreatment", lab_pretreatment)
    builder.add_node("lab_dispose", lab_dispose)
    builder.add_node("library_pretreatment", library_pretreatment)
    builder.add_node("library_dispose", library_dispose)
    builder.add_node("unsupported_node", unsupported_node)
    builder.add_node("output_node", output_node)

    builder.add_conditional_edges(
        START,
        route_affair,
        {
            "lab": "lab_pretreatment",
            "library": "library_pretreatment",
            "unsupported": "unsupported_node",
        },
    )

    # 校验失败也照常进 dispose：dispose 看到 affair_data 为空会跳过，原因已在状态里
    builder.add_edge("lab_pretreatment", "lab_dispose")
    builder.add_edge("library_pretreatment", "library_dispose")
    # 失败不重试：重复执行同一个节点不会让冲突消失，直接把原因交给输出
    builder.add_edge("lab_dispose", "output_node")
    builder.add_edge("library_dispose", "output_node")
    builder.add_edge("unsupported_node", "output_node")
    builder.add_edge("output_node", END)

    return builder.compile()


# 对外默认实例：app/tool/reservation_tool.py 的 make_reservation 直接用它
reservation_graph = build_reservation_graph()
# 兼容旧名字（原草稿里导出的就是 graph）
graph = reservation_graph
