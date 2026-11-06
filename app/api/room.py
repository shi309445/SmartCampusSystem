"""自习室与座位接口（学生浏览 / 管理员维护）

和 `app/api/lab.py` 一一对应：

    /api/labs        实验室      <->  /api/rooms       自习室
    /api/equipments  设备        <->  /api/seats       座位

**座位视图为什么要带 available**

学生选座时真正关心的是「这个时段这个座位空不空」，而不是座位表里的 status
（status 只表示「这个座位有没有被停用」）。所以 `/api/rooms/{id}/seats`
接受 start_time / end_time，把 `list_available_seats`（数据库里 NOT EXISTS 过滤）
的结果合并进座位列表，前端可以直接画座位图，不用再发一次请求。

自习室关闭时不查占座（service 会 409），直接把所有座位标成不可约——
详情页因此能正常展示一个「停用中」的自习室。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.common.response import Response
from app.database import get_db
from app.dependencies.auth import get_current_user, require_admin
from app.schemas.admin import RoomUpdateRequest, SeatBatchRequest, SeatUpdateRequest
from app.schemas.models import PageResponse
from app.schemas.portal import RoomSeatsOut, SeatAvailability
from app.schemas.table import User
from app.services.library import library_services
from app.services.library.models import (
    RoomCreate,
    RoomResponse,
    RoomUpdate,
    SeatBatchCreate,
    SeatResponse,
    SeatUpdate,
)

router = APIRouter(prefix="/api/rooms", tags=["自习室"])
seat_router = APIRouter(prefix="/api/seats", tags=["自习室·座位"])

__all__ = ["router", "seat_router"]

_DEFAULT_PAGE_SIZE = 12
_MAX_PAGE_SIZE = 100


# ======================================================================
# 一、自习室（登录可读）
# ======================================================================
@router.get(
    "",
    response_model=Response[PageResponse[RoomResponse]],
    summary="自习室列表",
)
def list_rooms(
    keyword: str | None = Query(None, max_length=50, description="关键词，匹配名称或位置"),
    capacity: int | None = Query(None, ge=1, description="按座位数上限筛选"),
    status: int | None = Query(None, ge=0, le=1, description="状态: 0-关闭, 1-开放"),
    page: int = Query(1, ge=1),
    page_size: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response[PageResponse[RoomResponse]]:
    result = library_services.get_room_page(
        db,
        page=page,
        page_size=page_size,
        keyword=keyword,
        capacity=capacity,
        status=status,
    )
    return Response.success(data=result)


@router.get(
    "/{room_id}",
    response_model=Response[RoomResponse],
    summary="自习室详情",
)
def get_room(
    room_id: int,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response[RoomResponse]:
    return Response.success(data=library_services.get_room_detail(db, room_id))


@router.get(
    "/{room_id}/seats",
    response_model=Response[RoomSeatsOut],
    summary="自习室座位视图（可带时段查空闲）",
)
def get_room_seats(
    room_id: int,
    start_time: datetime | None = Query(None, description="时段开始，如 2026-10-16T14:00:00"),
    end_time: datetime | None = Query(None, description="时段结束，与开始时间同时提供"),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response[RoomSeatsOut]:
    """不传时段 → 只按座位自身状态判断；传了 → 按该时段真实占座情况判断。"""
    if (start_time is None) != (end_time is None):
        raise BusinessException("开始时间与结束时间必须同时提供", code=400)

    room = library_services.get_room_detail(db, room_id)
    seats = library_services.list_seats(db, room_id)
    free_ids: set[int] | None = None

    if start_time is not None and end_time is not None:
        if end_time <= start_time:
            raise BusinessException("结束时间必须晚于开始时间", code=400)
        # 自习室关闭时不查占座（service 会 409），统一标成不可约
        if room.status == 1:
            free_ids = {seat.id for seat in library_services.list_available_seats(
                db, room_id, start_time, end_time
            )}

    items: list[SeatAvailability] = []
    for seat in seats:
        if free_ids is not None:
            available = seat.id in free_ids
        else:
            # 没按时段查：座位本身可用、且自习室开放，才算「可约」
            available = seat.status == 1 and room.status == 1
        items.append(SeatAvailability(**seat.model_dump(), available=available))

    return Response.success(
        data=RoomSeatsOut(
            room=room,
            day=start_time.date() if start_time is not None else None,
            start_time=start_time,
            end_time=end_time,
            total=len(items),
            available=sum(1 for item in items if item.available),
            seats=items,
        )
    )


# ======================================================================
# 二、自习室（管理员维护）
# ======================================================================
@router.post("", response_model=Response[RoomResponse], summary="新增自习室（管理员）")
def create_room(
    data: RoomCreate,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[RoomResponse]:
    return Response.success(message="自习室已创建", data=library_services.create_room(db, data))


@router.put("/{room_id}", response_model=Response[RoomResponse], summary="更新自习室（管理员）")
def update_room(
    room_id: int,
    data: RoomUpdateRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[RoomResponse]:
    payload = RoomUpdate(**data.model_dump(exclude_unset=True), id=room_id)
    return Response.success(message="自习室已更新", data=library_services.update_room(db, payload))


@router.delete(
    "/{room_id}",
    response_model=Response[dict[str, int]],
    summary="删除自习室（管理员）",
)
def delete_room(
    room_id: int,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[dict[str, int]]:
    library_services.delete_room(db, room_id)
    return Response.success(message="自习室已删除", data={"id": room_id})


@router.post(
    "/{room_id}/seats",
    response_model=Response[list[SeatResponse]],
    summary="给自习室批量新增座位（管理员）",
)
def add_seats(
    room_id: int,
    data: SeatBatchRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[list[SeatResponse]]:
    """自习室 id 只从路径取；座位总数不能超过自习室容量（service 拦截）。"""
    payload = SeatBatchCreate(room_id=room_id, seats=data.seats)
    result = library_services.add_seats(db, payload)
    return Response.success(message=f"已新增 {len(result)} 个座位", data=result)


# ======================================================================
# 三、座位（管理员维护；查询走 /api/rooms/{id}/seats）
# ======================================================================
@seat_router.put("/{seat_id}", response_model=Response[SeatResponse], summary="更新座位（管理员）")
def update_seat(
    seat_id: int,
    data: SeatUpdateRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[SeatResponse]:
    payload = SeatUpdate(**data.model_dump(exclude_unset=True), id=seat_id)
    return Response.success(message="座位已更新", data=library_services.update_seat(db, payload))


@seat_router.delete(
    "/{seat_id}",
    response_model=Response[dict[str, int]],
    summary="删除座位（管理员）",
)
def delete_seat(
    seat_id: int,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response[dict[str, int]]:
    library_services.delete_seat(db, seat_id)
    return Response.success(message="座位已删除", data={"id": seat_id})
