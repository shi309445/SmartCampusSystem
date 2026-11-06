"""实验室预约的业务规则（不改 HTTP 层，直接验证 service）

这些规则是「出了错会真出事」的那一档：时段冲突、容量、越权、审核状态机。
之前它们一条单测都没有，改一行代码只能靠手工点。
"""

from __future__ import annotations

from datetime import datetime, time
from uuid import uuid4

import pytest

from app.schemas.table import ROLE_ADMIN, ROLE_STUDENT
from app.services.laboratory import exceptions as lab_exc
from app.services.laboratory import lab_services
from app.services.laboratory.models import (
    ReservationCancel,
    ReservationCreate,
    ReservationReview,
)
from app.services.laboratory.table import (
    RESERVATION_STATUS_APPROVED,
    RESERVATION_STATUS_CANCELLED,
    RESERVATION_STATUS_PENDING,
    Lab,
)
from tests.conftest import at


def make_lab(
    db,
    *,
    status: int = 1,
    maxpeople: int = 30,
    open_hour: int = 8,
    close_hour: int = 22,
) -> Lab:
    lab = Lab(
        name=f"测试实验室-{uuid4().hex[:8]}",
        location="测试楼A101",
        maxpeople=maxpeople,
        open_time=time(open_hour, 0),
        close_time=time(close_hour, 0),
        status=status,
    )
    db.add(lab)
    db.commit()
    db.refresh(lab)
    return lab


def book(db, lab, student, start: datetime, end: datetime, *, people_num=None):
    return lab_services.create_reservation(
        db,
        ReservationCreate(
            lab_id=lab.id,
            reserve_start_time=start,
            reserve_end_time=end,
            people_num=people_num,
        ),
        user_id=student.id,
        user_name=student.name,
    )


def approve(db, reservation_id: int, admin) -> None:
    lab_services.review_reservation(
        db,
        ReservationReview(id=reservation_id, status=RESERVATION_STATUS_APPROVED, remark="测试通过"),
        handler_id=admin.id,
        handler_name=admin.name,
    )


# ==================================================================
# 一、提交预约
# ==================================================================
def test_create_returns_pending_reservation(db, student):
    lab = make_lab(db)
    result = book(db, lab, student, at(14), at(16))

    assert result.status == RESERVATION_STATUS_PENDING
    assert result.reservation_no.startswith("RES")
    assert result.lab_name == lab.name, "实验室名称要做快照，改名后历史记录仍可读"
    assert result.user_name == student.name


def test_pending_does_not_occupy_slot(db, student, other_student):
    """待审核不占时段：两个人可以同时申请同一时段（先到先审）"""
    lab = make_lab(db)
    book(db, lab, student, at(14), at(16))
    second = book(db, lab, other_student, at(14), at(16))

    assert second.status == RESERVATION_STATUS_PENDING


def test_approved_reservation_blocks_new_application(db, student, other_student, admin):
    lab = make_lab(db)
    first = book(db, lab, student, at(14), at(16))
    approve(db, first.id, admin)

    with pytest.raises(lab_exc.ReservationTimeConflictError):
        book(db, lab, other_student, at(15), at(17))


def test_back_to_back_slots_are_allowed(db, student, other_student, admin):
    """半开区间 [start, end)：10-11 与 11-12 不算冲突"""
    lab = make_lab(db)
    first = book(db, lab, student, at(10), at(11))
    approve(db, first.id, admin)

    second = book(db, lab, other_student, at(11), at(12))
    assert second.status == RESERVATION_STATUS_PENDING


def test_closed_lab_is_rejected(db, student):
    lab = make_lab(db, status=0)
    with pytest.raises(lab_exc.LabClosedError):
        book(db, lab, student, at(14), at(16))


def test_capacity_overflow_is_rejected(db, student):
    lab = make_lab(db, maxpeople=10)
    with pytest.raises(lab_exc.ReservationCapacityError):
        book(db, lab, student, at(14), at(16), people_num=11)


def test_outside_open_time_is_rejected(db, student):
    lab = make_lab(db, open_hour=8, close_hour=22)
    with pytest.raises(lab_exc.ReservationOutsideOpenTimeError):
        book(db, lab, student, at(7), at(9))


def test_past_time_is_rejected(db, student):
    lab = make_lab(db)
    with pytest.raises(lab_exc.ReservationTimeInvalidError):
        book(db, lab, student, at(14, days=-1), at(16, days=-1))


def test_cross_day_is_rejected(db, student):
    lab = make_lab(db)
    with pytest.raises(lab_exc.ReservationTimeInvalidError):
        book(db, lab, student, at(22, days=1), at(1, days=2))


# ==================================================================
# 二、审核
# ==================================================================
def test_review_approve_records_handler(db, student, admin):
    lab = make_lab(db)
    pending = book(db, lab, student, at(14), at(16))

    result = lab_services.review_reservation(
        db,
        ReservationReview(id=pending.id, status=RESERVATION_STATUS_APPROVED, remark="同意"),
        handler_id=admin.id,
        handler_name=admin.name,
    )

    assert result.status == RESERVATION_STATUS_APPROVED
    assert result.handler_id == admin.id
    assert result.handler_name == admin.name
    assert result.handle_time is not None
    assert result.remark == "同意"


def test_review_twice_is_rejected(db, student, admin):
    lab = make_lab(db)
    pending = book(db, lab, student, at(14), at(16))
    approve(db, pending.id, admin)

    with pytest.raises(lab_exc.ReservationStatusError):
        approve(db, pending.id, admin)


def test_review_reject_is_status_2(db, student, admin):
    lab = make_lab(db)
    pending = book(db, lab, student, at(14), at(16))

    result = lab_services.review_reservation(
        db,
        ReservationReview(id=pending.id, status=2, remark="该时段有课"),
        handler_id=admin.id,
        handler_name=admin.name,
    )
    assert result.status == 2


def test_review_approve_detects_conflict(db, student, other_student, admin):
    """两条待审核撞同一时段：先批那条成功，后批那条必须 409（否则同一时段被批成两条成功）"""
    lab = make_lab(db)
    first = book(db, lab, student, at(14), at(16))
    second = book(db, lab, other_student, at(15), at(17))

    approve(db, first.id, admin)
    with pytest.raises(lab_exc.ReservationTimeConflictError):
        approve(db, second.id, admin)


# ==================================================================
# 三、取消
# ==================================================================
def test_student_cannot_cancel_others(db, student, other_student):
    lab = make_lab(db)
    reservation = book(db, lab, other_student, at(14), at(16))

    with pytest.raises(lab_exc.ReservationPermissionError):
        lab_services.cancel_reservation(
            db,
            ReservationCancel(id=reservation.id, remark="越权尝试"),
            user_id=student.id,
            user_role=ROLE_STUDENT,
        )


def test_admin_can_cancel_anyone(db, student, admin):
    lab = make_lab(db)
    reservation = book(db, lab, student, at(14), at(16))

    result = lab_services.cancel_reservation(
        db,
        ReservationCancel(id=reservation.id, remark="实验室临时检修"),
        user_id=admin.id,
        user_role=ROLE_ADMIN,
    )
    assert result.status == RESERVATION_STATUS_CANCELLED


def test_cancel_releases_the_slot(db, student, other_student, admin):
    lab = make_lab(db)
    first = book(db, lab, student, at(14), at(16))
    approve(db, first.id, admin)

    with pytest.raises(lab_exc.ReservationTimeConflictError):
        book(db, lab, other_student, at(14), at(16))

    lab_services.cancel_reservation(
        db,
        ReservationCancel(id=first.id, remark="不去了"),
        user_id=student.id,
        user_role=ROLE_STUDENT,
    )
    after = book(db, lab, other_student, at(14), at(16))
    assert after.status == RESERVATION_STATUS_PENDING
