"""图书馆业务数据模型（Pydantic）

只服务图书馆业务，所以放在业务模块内部 app/services/library/models.py；
全项目通用的数据模型（ORMBase / RequestBase / PageResponse）在 app/schemas/models.py。
用户表（User）是全校园共用的，放在 app/schemas/table.py。

约定（与 app/services/laboratory/models.py 一致）：
    *_Create / *_Update  —— 上层（接口层 / Agent 工具）传入的请求模型
    *_Query              —— 条件查询模型
    *Response            —— 返回给上层的响应模型
"""

from datetime import datetime, time

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.models import ORMBase, PageResponse, RequestBase
from app.services.library.table import (
    RESERVATION_STATUS_APPROVED,
    RESERVATION_STATUS_REJECTED,
    STATUS_NORMAL,
)

__all__ = [
    "RoomCreate",
    "RoomUpdate",
    "RoomQuery",
    "RoomResponse",
    "SeatCreate",
    "SeatBatchCreate",
    "SeatUpdate",
    "SeatQuery",
    "SeatResponse",
    "ReservationCreate",
    "ReservationCancel",
    "ReservationReview",
    "ReservationQuery",
    "ReservationResponse",
    "TimeSlot",
    "RoomPage",
    "SeatPage",
    "ReservationPage",
    "ORMBase",
    "PageResponse",
]


def _strip_or_none(value: str | None) -> str | None:
    """去掉首尾空白；纯空白视作未填写（None），避免存入 "  " 这种脏数据"""
    if value is None:
        return None
    value = value.strip()
    return value or None


def _check_required_text(value: str, field_label: str) -> str:
    """必填文本：去空白后不能为空"""
    value = value.strip()
    if not value:
        raise ValueError(f"{field_label}不能为空")
    return value


# ====================== 自习室 ======================


class RoomCreate(RequestBase):
    """新增自习室请求模型"""

    name: str = Field(..., min_length=1, max_length=50, description="自习室名称")
    location: str | None = Field(None, max_length=100, description="位置(楼层/区域)")
    capacity: int = Field(..., ge=1, description="座位数上限")
    open_time: time | None = Field(None, description="开放开始时间")
    close_time: time | None = Field(None, description="开放结束时间")
    description: str | None = Field(None, max_length=2000, description="自习室简介")
    img: str | None = Field(None, max_length=200, description="封面图地址")
    status: int = Field(STATUS_NORMAL, ge=0, le=1, description="状态: 0-关闭, 1-开放")

    @field_validator("name")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _check_required_text(v, "自习室名称")

    @field_validator("location", "description", "img")
    @classmethod
    def strip_optional_text(cls, v: str | None) -> str | None:
        return _strip_or_none(v)

    @model_validator(mode="after")
    def check_open_time(self) -> "RoomCreate":
        """开放时间校验：只配置一半、或起止相同都是无效配置

        open_time > close_time 表示跨零点开放（如 22:00 - 次日 02:00），是合法配置。
        """
        if (self.open_time is None) != (self.close_time is None):
            raise ValueError("开放开始时间与结束时间必须同时配置")
        if self.open_time is not None and self.open_time == self.close_time:
            raise ValueError("开放开始时间与结束时间不能相同")
        return self


class RoomUpdate(RequestBase):
    """更新自习室请求模型（只传需要修改的字段）

    业务层用 exclude_unset 做增量更新；显式传 null 可以清空 location / description / img，
    name / capacity / status 对应数据库非空列，显式传 null 会被直接拒绝。
    """

    id: int = Field(..., gt=0, description="自习室ID")
    name: str | None = Field(None, min_length=1, max_length=50, description="自习室名称")
    location: str | None = Field(None, max_length=100, description="位置(楼层/区域)")
    capacity: int | None = Field(None, ge=1, description="座位数上限")
    open_time: time | None = Field(None, description="开放开始时间")
    close_time: time | None = Field(None, description="开放结束时间")
    description: str | None = Field(None, max_length=2000, description="自习室简介")
    img: str | None = Field(None, max_length=200, description="封面图地址")
    status: int | None = Field(None, ge=0, le=1, description="状态: 0-关闭, 1-开放")

    @field_validator("name")
    @classmethod
    def check_name(cls, v: str | None) -> str:
        if v is None:
            raise ValueError("自习室名称不能为空")
        return _check_required_text(v, "自习室名称")

    @field_validator("capacity")
    @classmethod
    def check_capacity(cls, v: int | None) -> int:
        if v is None:
            raise ValueError("座位数上限不能为空")
        return v

    @field_validator("status")
    @classmethod
    def check_status(cls, v: int | None) -> int:
        if v is None:
            raise ValueError("状态不能为空")
        return v

    @field_validator("location", "description", "img")
    @classmethod
    def strip_optional_text(cls, v: str | None) -> str | None:
        return _strip_or_none(v)

    @model_validator(mode="after")
    def check_open_time(self) -> "RoomUpdate":
        """同时修改开放时间时先校验一次；只改一个则由业务层与原值合并后再判断"""
        if (
            self.open_time is not None
            and self.close_time is not None
            and self.open_time == self.close_time
        ):
            raise ValueError("开放开始时间与结束时间不能相同")
        return self


class RoomQuery(RequestBase):
    """自习室条件查询模型"""

    room_ids: list[int] | None = Field(
        None, description="自习室ID列表，命中任意一个即可（多选为「或」关系）"
    )
    keyword: str | None = Field(
        None, max_length=50, description="关键词，匹配自习室名称或位置"
    )
    capacity: int | None = Field(None, ge=1, description="需求座位数，查询容量不小于该值的自习室")
    status: int | None = Field(None, ge=0, le=1, description="状态: 0-关闭, 1-开放")


class RoomResponse(ORMBase):
    """自习室响应模型"""

    id: int
    name: str
    location: str | None = None
    capacity: int
    open_time: time | None = None
    close_time: time | None = None
    description: str | None = None
    img: str | None = None
    status: int
    create_time: datetime
    update_time: datetime


# ====================== 座位 ======================


class SeatCreate(RequestBase):
    """单个座位请求模型（批量新增时使用）"""

    code: str = Field(..., min_length=1, max_length=50, description="座位编号，全库唯一")
    area: str | None = Field(None, max_length=50, description="座位区域(靠窗/静音区等)")
    has_power: bool = Field(False, description="是否有电源插座")
    description: str | None = Field(None, max_length=2000, description="座位描述")

    @field_validator("code")
    @classmethod
    def check_code(cls, v: str) -> str:
        return _check_required_text(v, "座位编号")

    @field_validator("area", "description")
    @classmethod
    def strip_optional_text(cls, v: str | None) -> str | None:
        return _strip_or_none(v)


class SeatBatchCreate(RequestBase):
    """批量新增座位请求模型"""

    room_id: int = Field(..., gt=0, description="所属自习室ID")
    seats: list[SeatCreate] = Field(
        ..., min_length=1, max_length=200, description="座位列表，单次最多 200 个"
    )


class SeatUpdate(RequestBase):
    """更新座位请求模型（只传需要修改的字段）"""

    id: int = Field(..., gt=0, description="座位ID")
    code: str | None = Field(None, min_length=1, max_length=50, description="座位编号")
    area: str | None = Field(None, max_length=50, description="座位区域")
    has_power: bool | None = Field(None, description="是否有电源插座")
    description: str | None = Field(None, max_length=2000, description="座位描述")
    status: int | None = Field(None, ge=0, le=1, description="状态: 0-不可用, 1-可用")

    @field_validator("code")
    @classmethod
    def check_code(cls, v: str | None) -> str:
        if v is None:
            raise ValueError("座位编号不能为空")
        return _check_required_text(v, "座位编号")

    @field_validator("status")
    @classmethod
    def check_status(cls, v: int | None) -> int:
        if v is None:
            raise ValueError("状态不能为空")
        return v

    @field_validator("area", "description")
    @classmethod
    def strip_optional_text(cls, v: str | None) -> str | None:
        return _strip_or_none(v)


class SeatQuery(RequestBase):
    """座位条件查询模型"""

    room_ids: list[int] | None = Field(
        None, description="自习室ID列表，命中任意一个即可（多选为「或」关系）"
    )
    keyword: str | None = Field(
        None, max_length=50, description="关键词，匹配座位编号、区域或描述"
    )
    status: int | None = Field(None, ge=0, le=1, description="状态: 0-不可用, 1-可用")
    has_power: bool | None = Field(None, description="是否只看有电源的座位")


class SeatResponse(ORMBase):
    """座位响应模型"""

    id: int
    room_id: int
    code: str
    area: str | None = None
    has_power: bool
    description: str | None = None
    status: int
    create_time: datetime
    update_time: datetime


# ====================== 座位预约 ======================


class ReservationCreate(RequestBase):
    """提交座位预约请求模型

    只接收 seat_id，不接收 seat_code / room_id / user_id / user_name：
    自习室与座位信息取自数据库，申请人身份由上层（登录态 / 调用上下文）传入，
    避免调用方伪造姓名或替别人占座。
    """

    seat_id: int = Field(..., gt=0, description="座位ID")
    reserve_start_time: datetime = Field(..., description="预约开始时间")
    reserve_end_time: datetime = Field(..., description="预约结束时间")

    @field_validator("reserve_start_time", "reserve_end_time")
    @classmethod
    def strip_timezone(cls, v: datetime) -> datetime:
        """统一成本地 naive 时间（数据库 DateTime 列不带时区）

        带时区的 ISO 时间（如 2026-09-20T10:00:00Z）直接与 naive 时间比较会抛 TypeError，
        这里先归一化。
        """
        if v.tzinfo is not None:
            return v.astimezone().replace(tzinfo=None)
        return v

    @model_validator(mode="after")
    def check_time_range(self) -> "ReservationCreate":
        if self.reserve_end_time <= self.reserve_start_time:
            raise ValueError("预约结束时间必须晚于开始时间")
        return self


class ReservationCancel(RequestBase):
    """取消预约请求模型"""

    id: int = Field(..., gt=0, description="预约ID")
    remark: str | None = Field(None, max_length=1000, description="取消原因")

    @field_validator("remark")
    @classmethod
    def strip_remark(cls, v: str | None) -> str | None:
        return _strip_or_none(v)


class ReservationReview(RequestBase):
    """管理员审核预约请求模型

    status 只允许 1-预约成功 / 2-预约失败；
    3-已取消 属于申请方动作，不通过审核接口传入。
    """

    id: int = Field(..., gt=0, description="预约ID")
    status: int = Field(..., description="审核结果: 1-预约成功, 2-预约失败")
    remark: str | None = Field(None, max_length=1000, description="处理说明")

    @field_validator("status")
    @classmethod
    def check_status(cls, v: int) -> int:
        if v not in (RESERVATION_STATUS_APPROVED, RESERVATION_STATUS_REJECTED):
            raise ValueError("审核结果只能是 1-预约成功 或 2-预约失败")
        return v

    @field_validator("remark")
    @classmethod
    def strip_remark(cls, v: str | None) -> str | None:
        return _strip_or_none(v)


class ReservationQuery(RequestBase):
    """预约条件查询模型"""

    status: int | list[int] | None = Field(
        None,
        description="预约状态，可传单个状态或状态列表: 0-待审核, 1-预约成功, 2-预约失败, 3-已取消",
    )
    room_ids: list[int] | None = Field(None, description="自习室ID列表，命中任意一个即可")
    seat_ids: list[int] | None = Field(None, description="座位ID列表，命中任意一个即可")
    user_id: int | None = Field(None, gt=0, description="申请人用户ID")
    handler_id: int | None = Field(None, gt=0, description="处理人用户ID")
    keyword: str | None = Field(
        None,
        max_length=50,
        description="关键词，匹配预约单号、自习室名称、座位编号或申请人姓名",
    )
    start_time: datetime | None = Field(
        None, description="时间段筛选：只看在该时间之后结束的预约"
    )
    end_time: datetime | None = Field(
        None, description="时间段筛选：只看在该时间之前开始的预约"
    )


class ReservationResponse(ORMBase):
    """预约记录响应模型（列表与详情通用）"""

    id: int
    reservation_no: str = Field(..., description="预约单号")

    room_id: int
    room_name: str

    seat_id: int
    seat_code: str

    user_id: int
    user_name: str

    reserve_start_time: datetime
    reserve_end_time: datetime

    status: int = Field(..., description="0-待审核, 1-预约成功, 2-预约失败, 3-已取消")
    remark: str | None = None

    handler_id: int | None = None
    handler_name: str | None = None
    handle_time: datetime | None = None

    create_time: datetime
    update_time: datetime


class TimeSlot(BaseModel):
    """某个座位在某天的可预约时段"""

    start_time: datetime = Field(..., description="时段开始时间")
    end_time: datetime = Field(..., description="时段结束时间")
    available: bool = Field(..., description="该时段是否可预约（False 表示已被预约成功的记录占用）")


# ====================== 分页响应别名（便于上层做类型标注与文档） ======================

RoomPage = PageResponse[RoomResponse]
SeatPage = PageResponse[SeatResponse]
ReservationPage = PageResponse[ReservationResponse]
