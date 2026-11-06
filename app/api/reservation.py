"""预约接口（学生自己的预约：查看 / 提交 / 取消）

**这条链路和智能助手那条有什么区别**

    智能助手   POST /api/chat → need_confirm → POST /api/chat/confirm
       模型只能「提议」，执行权在服务端，闸门见 app/harness/confirm.py。
       那道闸门防的是**模型**自作主张。

    本文件     POST /api/reservations/lab（表单提交，点「提交」就是确认）
       身份来自 JWT，服务端不再接受前端传的 user_id / user_name，
       防的是**伪造身份 / 替别人预约**。

两条入口最后都落到同一批 service 函数（`create_reservation` / `cancel_reservation`），
业务规则（开放时间、时段冲突、可提前天数、一人一座）只有一份实现。

**归属校验**

`get_reservation_detail` 自身不带权限判断（管理员也要看），所以详情接口在这里补一道：
不是本人就看不了（管理员例外）。取消则由 service 按 `user_role` 判断，学生只能取消自己的。

**为什么路径里区分 lab / library**

两张表主键各自自增，`id=1` 在两边都存在。用一个不区分来源的 `/api/reservations/{id}`
迟早会把实验室预约取消成座位预约（同一个坑见 app/api/admin_reservation.py 的说明）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.common.response import Response
from app.database import get_db
from app.dependencies.auth import get_current_user
from app.schemas.admin import CancelRequest
from app.schemas.models import PageResponse
from app.schemas.portal import MyReservationSummary, ReservationCounts
from app.schemas.table import ROLE_ADMIN, User
from app.services.laboratory import lab_services
from app.services.laboratory.models import ReservationCancel as LabReservationCancel
from app.services.laboratory.models import ReservationCreate as LabReservationCreate
from app.services.laboratory.models import ReservationResponse as LabReservationResponse
from app.services.laboratory.table import (
    RESERVATION_STATUS_APPROVED as LAB_APPROVED,
)
from app.services.laboratory.table import (
    RESERVATION_STATUS_CANCELLED as LAB_CANCELLED,
)
from app.services.laboratory.table import (
    RESERVATION_STATUS_PENDING as LAB_PENDING,
)
from app.services.laboratory.table import (
    RESERVATION_STATUS_REJECTED as LAB_REJECTED,
)
from app.services.library import library_services
from app.services.library.models import ReservationCancel as LibReservationCancel
from app.services.library.models import ReservationCreate as LibReservationCreate
from app.services.library.models import ReservationResponse as LibReservationResponse
from app.services.library.table import (
    RESERVATION_STATUS_APPROVED as LIB_APPROVED,
)
from app.services.library.table import (
    RESERVATION_STATUS_CANCELLED as LIB_CANCELLED,
)
from app.services.library.table import (
    RESERVATION_STATUS_PENDING as LIB_PENDING,
)
from app.services.library.table import (
    RESERVATION_STATUS_REJECTED as LIB_REJECTED,
)

router = APIRouter(prefix="/api/reservations", tags=["预约"])

__all__ = ["router"]

_DEFAULT_PAGE_SIZE = 10
_MAX_PAGE_SIZE = 100

_STATUS_QUERY = Query(
    None,
    description="预约状态，可重复传多个：0-待审核 1-预约成功 2-预约失败 3-已取消",
)


def _page_size(page_size: int) -> int:
    """单页条数收敛（service 层也会收敛，这里提前挡住明显越界）"""
    return max(1, min(page_size, _MAX_PAGE_SIZE))


# ======================================================================
# 一、我的预约（两类业务都提供，路径前缀区分来源）
# ======================================================================
@router.get(
    "/mine/summary",
    response_model=Response[MyReservationSummary],
    summary="我的预约概览（按状态统计）",
)
def my_reservation_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response[MyReservationSummary]:
    """学生首页用：一次拿到实验室 / 自习室两套业务的四种状态数量。

    内部仍然走分页查询（`page_size=1`），这样「我的预约」和统计永远同一套过滤条件，
    不会出现首页显示 3 条待审核、列表里却有 4 条的情况。
    """

    def _lab(status: int) -> int:
        return lab_services.get_user_reservation_page(
            db, current_user.id, page=1, page_size=1, status=status
        ).total

    def _library(status: int) -> int:
        return library_services.get_user_reservation_page(
            db, current_user.id, page=1, page_size=1, status=status
        ).total

    lab_counts = [_lab(LAB_PENDING), _lab(LAB_APPROVED), _lab(LAB_REJECTED), _lab(LAB_CANCELLED)]
    library_counts = [
        _library(LIB_PENDING),
        _library(LIB_APPROVED),
        _library(LIB_REJECTED),
        _library(LIB_CANCELLED),
    ]

    lab = ReservationCounts(
        pending=lab_counts[0],
        approved=lab_counts[1],
        rejected=lab_counts[2],
        cancelled=lab_counts[3],
        total=sum(lab_counts),
    )
    library = ReservationCounts(
        pending=library_counts[0],
        approved=library_counts[1],
        rejected=library_counts[2],
        cancelled=library_counts[3],
        total=sum(library_counts),
    )

    return Response.success(
        data=MyReservationSummary(
            lab=lab,
            library=library,
            pending_total=lab.pending + library.pending,
        )
    )


@router.get(
    "/lab/mine",
    response_model=Response[PageResponse[LabReservationResponse]],
    summary="我的实验室预约",
)
def my_lab_reservations(
    status: list[int] | None = _STATUS_QUERY,
    keyword: str | None = Query(None, max_length=50, description="单号 / 实验室 / 用途"),
    page: int = Query(1, ge=1),
    page_size: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response[PageResponse[LabReservationResponse]]:
    result = lab_services.get_user_reservation_page(
        db,
        current_user.id,
        page=page,
        page_size=_page_size(page_size),
        status=status,
        keyword=keyword,
    )
    return Response.success(data=result)


@router.get(
    "/library/mine",
    response_model=Response[PageResponse[LibReservationResponse]],
    summary="我的自习室座位预约",
)
def my_library_reservations(
    status: list[int] | None = _STATUS_QUERY,
    keyword: str | None = Query(None, max_length=50, description="单号 / 自习室 / 座位号"),
    page: int = Query(1, ge=1),
    page_size: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response[PageResponse[LibReservationResponse]]:
    result = library_services.get_user_reservation_page(
        db,
        current_user.id,
        page=page,
        page_size=_page_size(page_size),
        status=status,
        keyword=keyword,
    )
    return Response.success(data=result)


# ======================================================================
# 二、实验室预约：提交 / 详情 / 取消
# ======================================================================
@router.post(
    "/lab",
    response_model=Response[LabReservationResponse],
    summary="提交实验室预约",
)
def create_lab_reservation(
    data: LabReservationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response[LabReservationResponse]:
    """提交后是「待审核」，不占用时段；管理员通过后才算约上。"""
    result = lab_services.create_reservation(
        db, data, user_id=current_user.id, user_name=current_user.name
    )
    return Response.success(message="预约申请已提交，请等待管理员审核", data=result)


@router.get(
    "/lab/{reservation_id}",
    response_model=Response[LabReservationResponse],
    summary="实验室预约详情",
)
def get_lab_reservation(
    reservation_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response[LabReservationResponse]:
    detail = lab_services.get_reservation_detail(db, reservation_id)
    if detail.user_id != current_user.id and current_user.role != ROLE_ADMIN:
        raise BusinessException("只能查看自己的预约", code=403)
    return Response.success(data=detail)


@router.put(
    "/lab/{reservation_id}/cancel",
    response_model=Response[LabReservationResponse],
    summary="取消实验室预约",
)
def cancel_lab_reservation(
    reservation_id: int,
    data: CancelRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response[LabReservationResponse]:
    """学生只能取消自己的；管理员可以代取消（与 /api/admin/reservations/lab/{id}/cancel 等价）。"""
    result = lab_services.cancel_reservation(
        db,
        LabReservationCancel(id=reservation_id, remark=data.remark),
        user_id=current_user.id,
        user_role=current_user.role,
    )
    return Response.success(message="预约已取消", data=result)


# ======================================================================
# 三、自习室预约：提交 / 详情 / 取消
# ======================================================================
@router.post(
    "/library",
    response_model=Response[LibReservationResponse],
    summary="提交自习室座位预约",
)
def create_library_reservation(
    data: LibReservationCreate,
    auto_approve: bool = Query(
        False,
        description="True=先到先得、提交即成功；False（默认）=提交后等管理员审核",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response[LibReservationResponse]:
    result = library_services.create_reservation(
        db,
        data,
        user_id=current_user.id,
        user_name=current_user.name,
        auto_approve=auto_approve,
    )
    message = "座位预约成功" if auto_approve else "预约申请已提交，请等待管理员审核"
    return Response.success(message=message, data=result)


@router.get(
    "/library/{reservation_id}",
    response_model=Response[LibReservationResponse],
    summary="自习室座位预约详情",
)
def get_library_reservation(
    reservation_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response[LibReservationResponse]:
    detail = library_services.get_reservation_detail(db, reservation_id)
    if detail.user_id != current_user.id and current_user.role != ROLE_ADMIN:
        raise BusinessException("只能查看自己的预约", code=403)
    return Response.success(data=detail)


@router.put(
    "/library/{reservation_id}/cancel",
    response_model=Response[LibReservationResponse],
    summary="取消自习室座位预约",
)
def cancel_library_reservation(
    reservation_id: int,
    data: CancelRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response[LibReservationResponse]:
    result = library_services.cancel_reservation(
        db,
        LibReservationCancel(id=reservation_id, remark=data.remark),
        user_id=current_user.id,
        user_role=current_user.role,
    )
    return Response.success(message="预约已取消", data=result)
