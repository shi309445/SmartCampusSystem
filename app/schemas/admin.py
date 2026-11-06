"""管理员接口的数据模型（HTTP 层）

为什么单独放一个文件，而不是复用 `app/services/laboratory/models.py` 里的
`ReservationReview` / `ReservationCancel` / `LabUpdate`：

    那几个服务层模型里带着 `id` 字段（业务层用 `data.id` 定位记录）。
    HTTP 层已经用路径参数表达了 id（`/labs/{lab_id}`），再让前端在 body 里重复传一遍，
    就会出现「路径 id 与 body id 不一致该信谁」的问题——更糟的是前端忘了传直接 422。
    所以这里定义**只含业务字段**的请求模型，路由里把路径 id 合并回服务层模型：

        payload = LabUpdate(**data.model_dump(exclude_unset=True), id=lab_id)

`exclude_unset=True` 保留了「没传的字段 = 不改」的增量语义；服务层拿到的是完整模型，
名称查重 / 开放时间校验 / 非空列校验仍然只在服务层实现一次。

只有批量新增（设备 / 座位）不重复定义子项模型：子项本身没有 id，
直接复用 `EquipmentCreate` / `SeatCreate` 即可。
"""

from __future__ import annotations

from datetime import time

from pydantic import Field

from app.schemas.models import RequestBase
from app.services.laboratory.models import EquipmentCreate
from app.services.library.models import SeatCreate

__all__ = [
    "ReviewRequest",
    "CancelRequest",
    "LabUpdateRequest",
    "EquipmentUpdateRequest",
    "EquipmentBatchRequest",
    "RoomUpdateRequest",
    "SeatUpdateRequest",
    "SeatBatchRequest",
    "RESERVATION_REVIEW_STATUSES",
]

# 审核只能给出这两个结论（0-待审核 / 3-已取消 不是「审核结果」）
RESERVATION_REVIEW_STATUSES: tuple[int, int] = (1, 2)


# ======================================================================
# 一、预约
# ======================================================================
class ReviewRequest(RequestBase):
    """审核一个预约：通过 / 驳回"""

    status: int = Field(
        ...,
        description="审核结果: 1-预约成功（通过）, 2-预约失败（驳回）",
    )
    remark: str | None = Field(None, max_length=1000, description="处理说明 / 驳回原因")


class CancelRequest(RequestBase):
    """取消一个预约（管理员代取消，例如实验室临时检修）"""

    remark: str | None = Field(None, max_length=1000, description="取消原因")


# ======================================================================
# 二、实验室与设备（管理员维护）
# ======================================================================
class LabUpdateRequest(RequestBase):
    """更新实验室：只传需要修改的字段（id 在路径里）"""

    name: str | None = Field(None, min_length=1, max_length=50, description="实验室名称")
    location: str | None = Field(None, max_length=100, description="实验室位置")
    maxpeople: int | None = Field(None, ge=1, description="最大容纳人数")
    open_time: time | None = Field(None, description="开放开始时间")
    close_time: time | None = Field(None, description="开放结束时间")
    description: str | None = Field(None, max_length=2000, description="实验室简介")
    img: str | None = Field(None, max_length=200, description="封面图地址")
    status: int | None = Field(None, ge=0, le=1, description="状态: 0-关闭, 1-开放")


class EquipmentUpdateRequest(RequestBase):
    """更新设备：只传需要修改的字段（id 在路径里）"""

    name: str | None = Field(None, min_length=1, max_length=50, description="设备名称")
    code: str | None = Field(None, min_length=1, max_length=50, description="设备编号")
    model: str | None = Field(None, max_length=100, description="设备型号")
    description: str | None = Field(None, max_length=2000, description="设备描述")
    status: int | None = Field(None, ge=0, le=1, description="状态: 0-不可用, 1-可用")


class EquipmentBatchRequest(RequestBase):
    """给路径上的实验室批量新增设备（实验室 id 在路径里）"""

    equipments: list[EquipmentCreate] = Field(
        ..., min_length=1, max_length=100, description="设备列表，单次最多 100 台"
    )


# ======================================================================
# 三、自习室与座位（管理员维护）
# ======================================================================
class RoomUpdateRequest(RequestBase):
    """更新自习室：只传需要修改的字段（id 在路径里）

    name / capacity / status 对应数据库非空列，显式传 null 会被服务层拒绝（422）；
    location / description / img 显式传 null 表示清空。
    """

    name: str | None = Field(None, min_length=1, max_length=50, description="自习室名称")
    location: str | None = Field(None, max_length=100, description="位置(楼层/区域)")
    capacity: int | None = Field(None, ge=1, description="座位数上限")
    open_time: time | None = Field(None, description="开放开始时间")
    close_time: time | None = Field(None, description="开放结束时间")
    description: str | None = Field(None, max_length=2000, description="自习室简介")
    img: str | None = Field(None, max_length=200, description="封面图地址")
    status: int | None = Field(None, ge=0, le=1, description="状态: 0-关闭, 1-开放")


class SeatUpdateRequest(RequestBase):
    """更新座位：只传需要修改的字段（id 在路径里）"""

    code: str | None = Field(None, min_length=1, max_length=50, description="座位编号")
    area: str | None = Field(None, max_length=50, description="座位区域")
    has_power: bool | None = Field(None, description="是否有电源插座")
    description: str | None = Field(None, max_length=2000, description="座位描述")
    status: int | None = Field(None, ge=0, le=1, description="状态: 0-不可用, 1-可用")


class SeatBatchRequest(RequestBase):
    """给路径上的自习室批量新增座位（自习室 id 在路径里）"""

    seats: list[SeatCreate] = Field(
        ..., min_length=1, max_length=200, description="座位列表，单次最多 200 个"
    )
