"""实验室业务数据模型（Pydantic）

只服务实验室业务，所以放在业务模块内部 app/services/laboratory/models.py；
全项目通用的数据模型（ORMBase / PageResponse）在 app/schemas/models.py。

约定：
    *_Create / *_Update  —— 上层（接口层 / Agent 工具）传入的请求模型
    *_Query              —— 条件查询模型
    *Response            —— 返回给上层的响应模型
"""

from datetime import datetime, time

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.models import ORMBase, PageResponse
from app.services.laboratory.table import (
    RESERVATION_STATUS_APPROVED,
    RESERVATION_STATUS_REJECTED,
    STATUS_NORMAL,
)

__all__ = [
    "LabCreate",
    "LabUpdate",
    "LabQuery",
    "LabResponse",
    "EquipmentCreate",
    "EquipmentBatchCreate",
    "EquipmentUpdate",
    "EquipmentQuery",
    "EquipmentResponse",
    "ReservationCreate",
    "ReservationCancel",
    "ReservationReview",
    "ReservationQuery",
    "ReservationResponse",
    "TimeSlot",
    "LabPage",
    "EquipmentPage",
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


# ====================== 实验室 ======================


class LabCreate(BaseModel):
    """新增实验室请求模型"""

    name: str = Field(..., min_length=1, max_length=50, description="实验室名称")
    location: str | None = Field(None, max_length=100, description="实验室位置")
    maxpeople: int = Field(..., ge=1, description="最大容纳人数")
    open_time: time | None = Field(None, description="开放开始时间")
    close_time: time | None = Field(None, description="开放结束时间")
    description: str | None = Field(None, max_length=2000, description="实验室简介")
    img: str | None = Field(None, max_length=200, description="封面图地址")
    status: int = Field(STATUS_NORMAL, ge=0, le=1, description="状态: 0-关闭, 1-开放")

    @field_validator("name")
    @classmethod
    def check_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("实验室名称不能为空")
        return v

    @field_validator("location", "description", "img")
    @classmethod
    def strip_optional_text(cls, v: str | None) -> str | None:
        return _strip_or_none(v)

    @model_validator(mode="after")
    def check_open_time(self) -> "LabCreate":
        """开放时间校验：只配置一半、或起止相同都是无效配置

        open_time > close_time 表示跨零点开放（如 22:00 - 次日 02:00），是合法配置。
        """
        if (self.open_time is None) != (self.close_time is None):
            raise ValueError("开放开始时间与结束时间必须同时配置")
        if self.open_time is not None and self.open_time == self.close_time:
            raise ValueError("开放开始时间与结束时间不能相同")
        return self


class LabUpdate(BaseModel):
    """更新实验室请求模型（只传需要修改的字段）

    业务层用 exclude_unset 做增量更新，因此这里的字段全部可选；
    显式传 null 表示把该字段清空。
    """

    id: int = Field(..., gt=0, description="实验室ID")
    name: str | None = Field(None, min_length=1, max_length=50, description="实验室名称")
    location: str | None = Field(None, max_length=100, description="实验室位置")
    maxpeople: int | None = Field(None, ge=1, description="最大容纳人数")
    open_time: time | None = Field(None, description="开放开始时间")
    close_time: time | None = Field(None, description="开放结束时间")
    description: str | None = Field(None, max_length=2000, description="实验室简介")
    img: str | None = Field(None, max_length=200, description="封面图地址")
    status: int | None = Field(None, ge=0, le=1, description="状态: 0-关闭, 1-开放")

    @field_validator("name")
    @classmethod
    def check_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("实验室名称不能为空")
        return v

    @field_validator("location", "description", "img")
    @classmethod
    def strip_optional_text(cls, v: str | None) -> str | None:
        return _strip_or_none(v)

    @model_validator(mode="after")
    def check_open_time(self) -> "LabUpdate":
        """同时修改开放时间时校验；只改一个则交由业务层与原值合并后再判断"""
        if (
            self.open_time is not None
            and self.close_time is not None
            and self.open_time == self.close_time
        ):
            raise ValueError("开放开始时间与结束时间不能相同")
        return self


class LabQuery(BaseModel):
    """实验室条件查询模型"""

    lab_ids: list[int] | None = Field(
        None, description="实验室ID列表，命中任意一个即可（多选为「或」关系）"
    )
    keyword: str | None = Field(
        None, max_length=50, description="关键词，匹配实验室名称或位置"
    )
    people_num: int | None = Field(None, ge=1, description="需求人数，查询可容纳该人数的实验室")
    status: int | None = Field(None, ge=0, le=1, description="状态: 0-关闭, 1-开放")


class LabResponse(ORMBase):
    """实验室响应模型"""

    id: int
    name: str
    location: str | None = None
    maxpeople: int
    open_time: time | None = None
    close_time: time | None = None
    description: str | None = None
    img: str | None = None
    status: int
    create_time: datetime
    update_time: datetime


# ====================== 实验室设备 ======================


class EquipmentCreate(BaseModel):
    """单个设备请求模型（批量新增时使用）"""

    name: str = Field(..., min_length=1, max_length=50, description="设备名称")
    code: str = Field(..., min_length=1, max_length=50, description="设备编号，全库唯一")
    model: str | None = Field(None, max_length=100, description="设备型号")
    description: str | None = Field(None, max_length=2000, description="设备描述")

    @field_validator("name", "code")
    @classmethod
    def check_required_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("设备名称与编号不能为空")
        return v

    @field_validator("model", "description")
    @classmethod
    def strip_optional_text(cls, v: str | None) -> str | None:
        return _strip_or_none(v)


class EquipmentBatchCreate(BaseModel):
    """批量新增设备请求模型"""

    lab_id: int = Field(..., gt=0, description="所属实验室ID")
    equipments: list[EquipmentCreate] = Field(
        ..., min_length=1, max_length=100, description="设备列表，单次最多 100 台"
    )


class EquipmentUpdate(BaseModel):
    """更新设备请求模型（只传需要修改的字段）"""

    id: int = Field(..., gt=0, description="设备ID")
    name: str | None = Field(None, min_length=1, max_length=50, description="设备名称")
    code: str | None = Field(None, min_length=1, max_length=50, description="设备编号")
    model: str | None = Field(None, max_length=100, description="设备型号")
    description: str | None = Field(None, max_length=2000, description="设备描述")
    status: int | None = Field(None, ge=0, le=1, description="状态: 0-不可用, 1-可用")

    @field_validator("name", "code")
    @classmethod
    def check_required_text(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("设备名称与编号不能为空")
        return v

    @field_validator("model", "description")
    @classmethod
    def strip_optional_text(cls, v: str | None) -> str | None:
        return _strip_or_none(v)


class EquipmentQuery(BaseModel):
    """设备条件查询模型"""

    lab_ids: list[int] | None = Field(
        None, description="实验室ID列表，命中任意一个即可（多选为「或」关系）"
    )
    keyword: str | None = Field(
        None, max_length=50, description="关键词，匹配设备名称、编号或型号"
    )
    status: int | None = Field(None, ge=0, le=1, description="状态: 0-不可用, 1-可用")


class EquipmentResponse(ORMBase):
    """设备响应模型"""

    id: int
    lab_id: int
    name: str
    code: str
    model: str | None = None
    description: str | None = None
    status: int
    create_time: datetime
    update_time: datetime


# ====================== 实验室预约 ======================


class ReservationCreate(BaseModel):
    """提交预约申请请求模型

    只接收 lab_id，不接收 lab_name / user_id / user_name：
    实验室名称取自数据库、申请人身份由上层（登录态/调用上下文）传入，
    避免调用方伪造姓名或替别人预约。
    """

    lab_id: int = Field(..., gt=0, description="实验室ID")
    reserve_start_time: datetime = Field(..., description="预约开始时间")
    reserve_end_time: datetime = Field(..., description="预约结束时间")
    purpose: str | None = Field(None, max_length=500, description="使用目的/实验内容")
    people_num: int | None = Field(None, ge=1, description="使用人数")

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

    @field_validator("purpose")
    @classmethod
    def strip_purpose(cls, v: str | None) -> str | None:
        return _strip_or_none(v)

    @model_validator(mode="after")
    def check_time_range(self) -> "ReservationCreate":
        if self.reserve_end_time <= self.reserve_start_time:
            raise ValueError("预约结束时间必须晚于开始时间")
        return self


class ReservationCancel(BaseModel):
    """取消预约请求模型"""

    id: int = Field(..., gt=0, description="预约ID")
    remark: str | None = Field(None, max_length=1000, description="取消原因")

    @field_validator("remark")
    @classmethod
    def strip_remark(cls, v: str | None) -> str | None:
        return _strip_or_none(v)


class ReservationReview(BaseModel):
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


class ReservationQuery(BaseModel):
    """预约条件查询模型"""

    status: int | list[int] | None = Field(
        None, description="预约状态，可传单个状态或状态列表: 0-待审核, 1-预约成功, 2-预约失败, 3-已取消"
    )
    lab_ids: list[int] | None = Field(
        None, description="实验室ID列表，命中任意一个即可（多选为「或」关系）"
    )
    user_id: int | None = Field(None, gt=0, description="申请人用户ID")
    handler_id: int | None = Field(None, gt=0, description="处理人用户ID")
    keyword: str | None = Field(
        None, max_length=50, description="关键词，匹配预约单号、实验室名称、申请人姓名或使用目的"
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

    lab_id: int
    lab_name: str

    user_id: int
    user_name: str

    purpose: str | None = None
    people_num: int | None = None

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
    """实验室某天的可预约时段"""

    start_time: datetime = Field(..., description="时段开始时间")
    end_time: datetime = Field(..., description="时段结束时间")
    available: bool = Field(..., description="该时段是否可预约（False 表示已被审核通过的预约占用）")


# ====================== 分页响应别名（便于上层做类型标注与文档） ======================

LabPage = PageResponse[LabResponse]
EquipmentPage = PageResponse[EquipmentResponse]
ReservationPage = PageResponse[ReservationResponse]
