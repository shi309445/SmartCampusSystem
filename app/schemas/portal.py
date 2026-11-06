"""前端门户（Web UI）专用的组合响应模型

**为什么单独一个文件**

`app/services/*/models.py` 里的 `*Response` 是「一张表 / 一次业务调用」的模型，
而前端详情页往往需要把两三个业务对象拼在一起返回（实验室 + 当天排期 + 可预约时段、
自习室 + 座位 + 每个座位在该时段是否空闲）。如果让前端串行发三四个请求，
既多一轮往返，也容易拼出互相矛盾的快照（比如时段和座位不是同一个时间点查的）。

所以这里集中放「只被接口层用来拼组合响应」的模型：

    LabDayOut            实验室详情页：实验室 + 某天排期 + 可预约时段
    SeatAvailability     座位 + 该时段是否空闲
    RoomSeatsOut         自习室详情页：自习室 + 全部座位 + 空闲统计
    ReservationCounts    某一类预约按状态的数量
    MyReservationSummary 我的预约概览（实验室 + 自习室）

它们**不参与任何业务规则**，也不进 services 层；改这里的字段只影响 HTTP 契约
（前端 `src/schemas/*.ts` 里有一一对应的类型声明）。
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.services.laboratory.models import LabResponse
from app.services.laboratory.models import ReservationResponse as LabReservationResponse
from app.services.laboratory.models import TimeSlot as LabTimeSlot
from app.services.library.models import RoomResponse, SeatResponse

__all__ = [
    "LabDayOut",
    "SeatAvailability",
    "RoomSeatsOut",
    "ReservationCounts",
    "MyReservationSummary",
]


# ======================================================================
# 一、实验室
# ======================================================================
class LabDayOut(BaseModel):
    """实验室某天的占用情况（实验室详情页一次拿全）

    slots 为空有两种情况：实验室已关闭 / 当天已无可约时段（开放时间已过）。
    前端据此决定是显示「今天约不上了」还是「实验室停用中」，所以 lab.status 也在里面。
    """

    lab: LabResponse = Field(..., description="实验室基本信息")
    day: date = Field(..., description="查询的日期")
    schedule: list[LabReservationResponse] = Field(
        default_factory=list, description="当天已有预约（含待审核，按开始时间排序）"
    )
    slots: list[LabTimeSlot] = Field(
        default_factory=list, description="按整点切分的可预约时段；实验室关闭时为空"
    )


# ======================================================================
# 二、自习室座位
# ======================================================================
class SeatAvailability(SeatResponse):
    """座位 + 在指定时段是否空闲

    继承 SeatResponse 保留座位本身的全部字段（编号 / 区域 / 电源 / 状态），
    额外一个 available 表示「这个时间段能不能约」。
    """

    available: bool = Field(True, description="该时段是否可预约（False=已被预约成功占用或座位停用）")


class RoomSeatsOut(BaseModel):
    """自习室详情页的座位视图

    不传 start_time / end_time 时按座位自身状态判断（status=1 即空闲），
    传了就按该时段真实占座情况判断。
    """

    room: RoomResponse = Field(..., description="自习室基本信息")
    day: date | None = Field(None, description="查询日期（未按时段查询时为空）")
    start_time: datetime | None = Field(None, description="查询时段开始（未传则为空）")
    end_time: datetime | None = Field(None, description="查询时段结束（未传则为空）")
    total: int = Field(0, description="座位总数")
    available: int = Field(0, description="该时段空闲座位数")
    seats: list[SeatAvailability] = Field(default_factory=list, description="全部座位")


# ======================================================================
# 三、我的预约概览
# ======================================================================
class ReservationCounts(BaseModel):
    """某一类预约（实验室 / 自习室）按状态统计的数量"""

    pending: int = Field(0, description="待审核")
    approved: int = Field(0, description="预约成功")
    rejected: int = Field(0, description="预约失败")
    cancelled: int = Field(0, description="已取消")
    total: int = Field(0, description="合计")


class MyReservationSummary(BaseModel):
    """学生首页的预约概览（一次请求拿到两套业务的状态分布）"""

    lab: ReservationCounts = Field(default_factory=ReservationCounts)
    library: ReservationCounts = Field(default_factory=ReservationCounts)
    pending_total: int = Field(0, description="两类业务加起来的待审核数量")
