"""自习室座位预约的业务规则

和实验室的区别：实验室占「整间房」，自习室占「一个座位」——
所以冲突按 seat_id 算，而且同一个人在同一时段只能占一个座位。
"""

from __future__ import annotations

from datetime import time
from uuid import uuid4

import pytest

from app.services.library import exceptions as lib_exc
from app.services.library import library_services
from app.services.library.models import ReservationCreate, ReservationReview
from app.services.library.table import (
    RESERVATION_STATUS_APPROVED,
    RESERVATION_STATUS_PENDING,
    Seat,
    StudyRoom,
)
from tests.conftest import at


def make_room(db, *, status: int = 1, open_hour: int = 8, close_hour: int = 22) -> StudyRoom:
    room = StudyRoom(
        name=f"测试自习室-{uuid4().hex[:8]}",
        location="图书馆3F",
        capacity=50,
        open_time=time(open_hour, 0),
        close_time=time(close_hour, 0),
        status=status,
    )
    db.add(room)
    db.commit()
    db.refresh(room)
    return room


def make_seat(db, room: StudyRoom, *, status: int = 1) -> Seat:
    seat = Seat(
        code=f"SEAT-{uuid4().hex[:8]}",
        area="靠窗",
        has_power=True,
        status=status,
        room_id=room.id,
    )
    db.add(seat)
    db.commit()
    db.refresh(seat)
    return seat


def book(db, seat, student, start, end, *, auto_approve: bool = False):
    return library_services.create_reservation(
        db,
        ReservationCreate(seat_id=seat.id, reserve_start_time=start, reserve_end_time=end),
        user_id=student.id,
        user_name=student.name,
        auto_approve=auto_approve,
    )


# ==================================================================
# 一、提交预约
# ==================================================================
def test_default_is_pending(db, student):
    room = make_room(db)
    seat = make_seat(db, room)
    result = book(db, seat, student, at(14), at(16))

    assert result.status == RESERVATION_STATUS_PENDING
    assert result.reservation_no.startswith("LIB")
    assert result.seat_code == seat.code, "座位号要做快照"


def test_auto_approve_is_approved(db, student):
    """先到先得模式：提交即成功（对话里的默认行为）"""
    room = make_room(db)
    seat = make_seat(db, room)
    result = book(db, seat, student, at(14), at(16), auto_approve=True)

    assert result.status == RESERVATION_STATUS_APPROVED


def test_approved_seat_blocks_others(db, student, other_student):
    room = make_room(db)
    seat = make_seat(db, room)
    book(db, seat, student, at(14), at(16), auto_approve=True)

    with pytest.raises(lib_exc.ReservationTimeConflictError):
        book(db, seat, other_student, at(15), at(17), auto_approve=True)


def test_same_user_cannot_hold_two_seats(db, student):
    """一个人同一时段只能占一个座位（否则可以霸座）"""
    room = make_room(db)
    seat_a = make_seat(db, room)
    seat_b = make_seat(db, room)

    book(db, seat_a, student, at(14), at(16), auto_approve=True)

    with pytest.raises(lib_exc.ReservationUserConflictError):
        book(db, seat_b, student, at(15), at(17), auto_approve=True)


def test_different_seats_same_room_are_fine(db, student, other_student):
    """同一间自习室、不同座位、同一时段 —— 这是正常场景"""
    room = make_room(db)
    seat_a = make_seat(db, room)
    seat_b = make_seat(db, room)

    book(db, seat_a, student, at(14), at(16), auto_approve=True)
    second = book(db, seat_b, other_student, at(14), at(16), auto_approve=True)

    assert second.status == RESERVATION_STATUS_APPROVED


def test_reservation_longer_than_four_hours_is_rejected(db, student):
    room = make_room(db)
    seat = make_seat(db, room)
    with pytest.raises(lib_exc.ReservationTimeInvalidError):
        book(db, seat, student, at(8), at(13))


def test_reservation_beyond_three_days_is_rejected(db, student):
    room = make_room(db)
    seat = make_seat(db, room)
    with pytest.raises(lib_exc.ReservationTimeInvalidError):
        book(db, seat, student, at(14, days=4), at(16, days=4))


def test_closed_room_is_rejected(db, student):
    room = make_room(db, status=0)
    seat = make_seat(db, room)
    with pytest.raises(lib_exc.RoomClosedError):
        book(db, seat, student, at(14), at(16))


def test_unavailable_seat_is_rejected(db, student):
    room = make_room(db)
    seat = make_seat(db, room, status=0)
    with pytest.raises(lib_exc.SeatUnavailableError):
        book(db, seat, student, at(14), at(16))


# ==================================================================
# 二、审核（auto_approve=False 的那条路）
# ==================================================================
def test_review_approve_detects_seat_conflict(db, student, other_student, admin):
    room = make_room(db)
    seat = make_seat(db, room)
    first = book(db, seat, student, at(14), at(16))
    second = book(db, seat, other_student, at(15), at(17))

    library_services.review_reservation(
        db,
        ReservationReview(id=first.id, status=RESERVATION_STATUS_APPROVED),
        handler_id=admin.id,
        handler_name=admin.name,
    )
    with pytest.raises(lib_exc.ReservationTimeConflictError):
        library_services.review_reservation(
            db,
            ReservationReview(id=second.id, status=RESERVATION_STATUS_APPROVED),
            handler_id=admin.id,
            handler_name=admin.name,
        )
