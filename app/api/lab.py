"""实验室与设备接口（学生浏览 / 管理员维护）

**为什么会有这个文件**

`app/services/laboratory` 里的业务能力（分页查询、当天排期、可预约时段、设备增删改）
早就写完了，但接口层只有「预约审核」那一组，前端拿不到实验室列表，详情页也没法
知道「今天还剩哪些时段」——页面只能靠猜 id。这个文件把前端需要的那层薄壳补上。

**读写分开的权限**

    GET  /api/labs...           登录即可（学生要看实验室才能预约）
    GET  /api/equipments...     登录即可（设备清单是公开信息）
    POST/PUT/DELETE ...         仅管理员（require_admin，非管理员 403）

路由函数仍然只做三件事：参数校验 → 调 service → 包信封（app/common/response.py）。
领域规则（名称查重、开放时间校验、删除时有没有预约）全部留在 service 层。

**设备为什么单独一个 router**

设备的主键在整个项目里是全局的（`/api/equipments/{id}` 就能定位到唯一一条），
所以它的增删改不挂在 `/api/labs/{lab_id}/...` 下面；而「查某个实验室的设备」
属于实验室视角的只读视图，就留在 labs 路由里。两者共用下面的 `equipment_router` / 实验室路由。
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.common.response import Response
from app.database import get_db
from app.dependencies.auth import get_current_user, require_admin
from app.schemas.admin import (
    EquipmentBatchRequest,
    EquipmentUpdateRequest,
    LabUpdateRequest,
)
from app.schemas.models import PageResponse
from app.schemas.portal import LabDayOut
from app.schemas.table import User
from app.services.laboratory import lab_services
from app.services.laboratory.exceptions import LabClosedError
from app.services.laboratory.models import (
    EquipmentBatchCreate,
    EquipmentResponse,
    EquipmentUpdate,
    LabCreate,
    LabResponse,
    LabUpdate,
)

router = APIRouter(prefix="/api/labs", tags=["实验室"])
equipment_router = APIRouter(prefix="/api/equipments", tags=["实验室·设备"])

__all__ = ["router", "equipment_router"]

# 分页参数：与 service 层的 MAX_PAGE_SIZE 对齐（超限由 service 静默收敛）
_DEFAULT_PAGE_SIZE = 12
_MAX_PAGE_SIZE = 100

_STATUS_QUERY = Query(None, ge=0, le=1, description="状态: 0-关闭, 1-开放")


# ======================================================================
# 一、实验室（登录可读）
# ======================================================================
@router.get(
    "",
    response_model=Response[PageResponse[LabResponse]],
    summary="实验室列表",
)
def list_labs(
    keyword: str | None = Query(None, max_length=50, description="关键词，匹配名称或位置"),
    people_num: int | None = Query(None, ge=1, description="按容纳人数筛选"),
    status: int | None = _STATUS_QUERY,
    page: int = Query(1, ge=1),
    page_size: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response[PageResponse[LabResponse]]:
    """学生浏览实验室 / 管理员管理实验室共用；`?status=1` 只看开放中的。"""
    result = lab_services.get_lab_page(
        db,
        page=page,
        page_size=page_size,
        keyword=keyword,
        people_num=people_num,
        status=status,
    )
    return Response.success(data=result)


@router.get(
    "/{lab_id}",
    response_model=Response[LabResponse],
    summary="实验室详情",
)
def get_lab(
    lab_id: int,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response[LabResponse]:
    """关闭状态的实验室也能看（详情页要展示「停用中」而不是 404）。"""
    return Response.success(data=lab_services.get_lab_detail(db, lab_id))


@router.get(
    "/{lab_id}/schedule",
    response_model=Response[LabDayOut],
    summary="实验室某天的排期与可预约时段",
)
def get_lab_schedule(
    lab_id: int,
    day: date | None = Query(None, description="日期，默认今天"),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response[LabDayOut]:
    """一次返回：实验室信息 + 当天已有预约（含待审核）+ 按整点切分的可预约时段。

    实验室关闭时不查时段（service 会抛 409），直接给空列表——
    详情页据此显示「实验室停用中」，而不是弹一个错误提示。
    """
    target_day = day or date.today()
    lab = lab_services.get_lab_detail(db, lab_id)
    schedule = lab_services.get_lab_schedule(db, lab_id, target_day)

    slots = []
    if lab.status == 1:
        try:
            slots = lab_services.list_available_slots(db, lab_id, target_day)
        except LabClosedError:
            slots = []

    return Response.success(
        data=LabDayOut(lab=lab, day=target_day, schedule=schedule, slots=slots)
    )


@router.get(
    "/{lab_id}/equipments",
    response_model=Response[list[EquipmentResponse]],
    summary="某个实验室的设备清单",
)
def list_lab_equipments(
    lab_id: int,
    keyword: str | None = Query(None, max_length=50, description="匹配名称、编号或型号"),
    status: int | None = Query(None, ge=0, le=1, description="状态: 0-不可用, 1-可用"),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response[list[EquipmentResponse]]:
    """实验室不存在时 404（由 service 抛 LabNotFoundError）。"""
    return Response.success(
        data=lab_services.list_equipments(db, lab_id, keyword=keyword, status=status)
    )


# ======================================================================
# 二、实验室（管理员维护）
# ======================================================================
@router.post(
    "",
    response_model=Response[LabResponse],
    summary="新增实验室（管理员）",
)
def create_lab(
    data: LabCreate,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[LabResponse]:
    return Response.success(message="实验室已创建", data=lab_services.create_lab(db, data))


@router.put(
    "/{lab_id}",
    response_model=Response[LabResponse],
    summary="更新实验室（管理员）",
)
def update_lab(
    lab_id: int,
    data: LabUpdateRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[LabResponse]:
    """只传需要修改的字段（id 在路径里，body 不用重复传）。"""
    payload = LabUpdate(**data.model_dump(exclude_unset=True), id=lab_id)
    return Response.success(message="实验室已更新", data=lab_services.update_lab(db, payload))


@router.delete(
    "/{lab_id}",
    response_model=Response[dict[str, int]],
    summary="删除实验室（管理员）",
)
def delete_lab(
    lab_id: int,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[dict[str, int]]:
    """存在预约记录时 409（service 层拦截），设备随实验室级联删除。"""
    lab_services.delete_lab(db, lab_id)
    return Response.success(message="实验室已删除", data={"id": lab_id})


@router.post(
    "/{lab_id}/equipments",
    response_model=Response[list[EquipmentResponse]],
    summary="给实验室批量新增设备（管理员）",
)
def add_equipments(
    lab_id: int,
    data: EquipmentBatchRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[list[EquipmentResponse]]:
    """实验室 id 只从路径取，防止给 A 实验室加设备却在 body 里传成 B。"""
    payload = EquipmentBatchCreate(lab_id=lab_id, equipments=data.equipments)
    result = lab_services.add_equipments(db, payload)
    return Response.success(message=f"已新增 {len(result)} 台设备", data=result)


# ======================================================================
# 三、设备（全局，登录可读 / 管理员可写）
# ======================================================================
@equipment_router.get(
    "",
    response_model=Response[PageResponse[EquipmentResponse]],
    summary="设备列表",
)
def list_equipments(
    lab_id: int | None = Query(None, gt=0, description="只看某个实验室的设备"),
    keyword: str | None = Query(None, max_length=50, description="匹配名称、编号或型号"),
    status: int | None = Query(None, ge=0, le=1, description="状态: 0-不可用, 1-可用"),
    page: int = Query(1, ge=1),
    page_size: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response[PageResponse[EquipmentResponse]]:
    result = lab_services.get_equipment_page(
        db,
        page=page,
        page_size=page_size,
        lab_ids=[lab_id] if lab_id is not None else None,
        keyword=keyword,
        status=status,
    )
    return Response.success(data=result)


@equipment_router.put(
    "/{equipment_id}",
    response_model=Response[EquipmentResponse],
    summary="更新设备（管理员）",
)
def update_equipment(
    equipment_id: int,
    data: EquipmentUpdateRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[EquipmentResponse]:
    payload = EquipmentUpdate(**data.model_dump(exclude_unset=True), id=equipment_id)
    return Response.success(message="设备已更新", data=lab_services.update_equipment(db, payload))


@equipment_router.delete(
    "/{equipment_id}",
    response_model=Response[dict[str, int]],
    summary="删除设备（管理员）",
)
def delete_equipment(
    equipment_id: int,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[dict[str, int]]:
    lab_services.delete_equipment(db, equipment_id)
    return Response.success(message="设备已删除", data={"id": equipment_id})
