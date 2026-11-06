"""管理员接口：预约审核闭环（实验室 / 图书馆自习室）

**为什么需要这个文件**

在此之前，服务层把预约审核写完了（`lab_services.review_reservation` /
`library_services.review_reservation`：加锁、复检冲突、记录处理人），
但**接口层一个路由都没有** —— 学生提交的预约永远停在 `status=0 待审核`。
这个文件把那条断掉的链路接上，顺便补上管理员查看与代取消。

**两套业务共用一套路径形状**

    实验室        /api/admin/reservations/lab/{id}/...
    自习室座位    /api/admin/reservations/library/{id}/...

不用 `/reservations/{id}` 是刻意的：两张表的主键各自自增，`id=1` 在两边都存在，
用一个不区分来源的路径必然有一天把实验室预约审成座位预约。

**权限**

所有路由都挂 `require_admin`（非管理员 403）。处理人身份从登录态取，
不接受前端传 `handler_id` —— 否则可以伪造审批人。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.common.response import Response
from app.database import get_db
from app.dependencies.auth import require_admin
from app.schemas.admin import RESERVATION_REVIEW_STATUSES, CancelRequest, ReviewRequest
from app.schemas.models import PageResponse
from app.schemas.table import User
from app.services.laboratory import lab_services
from app.services.laboratory.models import (
    ReservationCancel as LabReservationCancel,
)
from app.services.laboratory.models import (
    ReservationResponse as LabReservationResponse,
)
from app.services.laboratory.models import (
    ReservationReview as LabReservationReview,
)
from app.services.library import library_services
from app.services.library.models import (
    ReservationCancel as LibReservationCancel,
)
from app.services.library.models import (
    ReservationResponse as LibReservationResponse,
)
from app.services.library.models import (
    ReservationReview as LibReservationReview,
)

router = APIRouter(prefix="/api/admin/reservations", tags=["管理员·预约"])

__all__ = ["router"]

# 分页参数：与 service 层的 MAX_PAGE_SIZE 对齐，超过会被静默收敛（service 里兜底）
_DEFAULT_PAGE_SIZE = 10
_MAX_PAGE_SIZE = 100

_STATUS_QUERY = Query(
    None,
    description="预约状态，可重复传多个：0-待审核 1-预约成功 2-预约失败 3-已取消",
)


def _check_review_status(status: int) -> None:
    """审核只能给 1/2；其他值在 service 层会被当成状态写进库里，必须提前拦"""
    if status not in RESERVATION_REVIEW_STATUSES:
        raise BusinessException("审核结果只能是 1（通过）或 2（驳回）", code=400)


# ==================================================================
# 一、实验室预约
# ==================================================================
@router.get(
    "/lab",
    response_model=Response[PageResponse[LabReservationResponse]],
    summary="实验室预约列表（管理员）",
)
def list_lab_reservations(
    status: list[int] | None = _STATUS_QUERY,
    keyword: str | None = Query(None, max_length=50, description="预约单号 / 实验室 / 申请人"),
    page: int = Query(1, ge=1),
    page_size: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[PageResponse[LabReservationResponse]]:
    """不传 status 查全部；审核待办用 `?status=0`"""
    result = lab_services.get_all_reservation_page(
        db, page=page, page_size=page_size, status=status, keyword=keyword
    )
    return Response.success(data=result)


@router.put(
    "/lab/{reservation_id}/review",
    response_model=Response[LabReservationResponse],
    summary="审核实验室预约（管理员）",
)
def review_lab_reservation(
    reservation_id: int,
    data: ReviewRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[LabReservationResponse]:
    """通过 / 驳回。审核通过前会复检时段冲突，冲突则 409。"""
    _check_review_status(data.status)
    result = lab_services.review_reservation(
        db,
        LabReservationReview(id=reservation_id, status=data.status, remark=data.remark),
        handler_id=admin.id,
        handler_name=admin.name,
    )
    return Response.success(message="审核完成", data=result)


@router.put(
    "/lab/{reservation_id}/cancel",
    response_model=Response[LabReservationResponse],
    summary="代取消实验室预约（管理员）",
)
def cancel_lab_reservation(
    reservation_id: int,
    data: CancelRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[LabReservationResponse]:
    """管理员可以取消任意学生的预约（例如实验室临时检修）"""
    result = lab_services.cancel_reservation(
        db,
        LabReservationCancel(id=reservation_id, remark=data.remark),
        user_id=admin.id,
        user_role=admin.role,
    )
    return Response.success(message="预约已取消", data=result)


# ==================================================================
# 二、自习室座位预约
# ==================================================================
@router.get(
    "/library",
    response_model=Response[PageResponse[LibReservationResponse]],
    summary="自习室座位预约列表（管理员）",
)
def list_library_reservations(
    status: list[int] | None = _STATUS_QUERY,
    keyword: str | None = Query(None, max_length=50, description="预约单号 / 自习室 / 申请人"),
    page: int = Query(1, ge=1),
    page_size: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[PageResponse[LibReservationResponse]]:
    result = library_services.get_all_reservation_page(
        db, page=page, page_size=page_size, status=status, keyword=keyword
    )
    return Response.success(data=result)


@router.put(
    "/library/{reservation_id}/review",
    response_model=Response[LibReservationResponse],
    summary="审核自习室座位预约（管理员）",
)
def review_library_reservation(
    reservation_id: int,
    data: ReviewRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[LibReservationResponse]:
    _check_review_status(data.status)
    result = library_services.review_reservation(
        db,
        LibReservationReview(id=reservation_id, status=data.status, remark=data.remark),
        handler_id=admin.id,
        handler_name=admin.name,
    )
    return Response.success(message="审核完成", data=result)


@router.put(
    "/library/{reservation_id}/cancel",
    response_model=Response[LibReservationResponse],
    summary="代取消自习室座位预约（管理员）",
)
def cancel_library_reservation(
    reservation_id: int,
    data: CancelRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[LibReservationResponse]:
    result = library_services.cancel_reservation(
        db,
        LibReservationCancel(id=reservation_id, remark=data.remark),
        user_id=admin.id,
        user_role=admin.role,
    )
    return Response.success(message="预约已取消", data=result)
