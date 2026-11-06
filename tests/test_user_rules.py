"""用户与鉴权规则：注册 / 登录 / 改密码 / 管理员自保护 / 删除引用检查"""

from __future__ import annotations

from datetime import time
from uuid import uuid4

import pytest

from app.schemas.table import ROLE_ADMIN, ROLE_STUDENT, USER_STATUS_DISABLED
from app.services.laboratory import lab_services
from app.services.laboratory.models import ReservationCreate
from app.services.laboratory.table import Lab
from app.services.user import auth_services, user_services
from app.services.user.exceptions import (
    PasswordIncorrectError,
    PasswordUnchangedError,
    UserAuthenticationError,
    UserDisabledError,
    UserReferenceConflictError,
    UserSelfOperationError,
    UsernameDuplicateError,
)
from app.services.user.models import (
    LoginRequest,
    PasswordReset,
    PasswordUpdate,
    RegisterRequest,
    UserUpdate,
)
from tests.conftest import at, make_user

# ==================================================================
# 一、注册 / 登录
# ==================================================================
def test_register_forces_student_role(db):
    """注册接口不接受角色参数：任何人都不能注册出管理员"""
    result = auth_services.register(
        db, RegisterRequest(username="20219999", password="Student@123456", name="新生")
    )
    assert result.role == ROLE_STUDENT
    assert result.status == 1


def test_register_duplicate_username(db, student):
    with pytest.raises(UsernameDuplicateError):
        auth_services.register(
            db, RegisterRequest(username=student.username, password="Student@123456")
        )


def test_login_with_wrong_password(db, student):
    with pytest.raises(UserAuthenticationError):
        auth_services.login(
            db, LoginRequest(username=student.username, password="definitely-wrong")
        )


def test_login_disabled_account(db):
    disabled = make_user(db, "20210003", status=USER_STATUS_DISABLED, name="陈小刚")
    with pytest.raises(UserDisabledError):
        auth_services.login(
            db, LoginRequest(username=disabled.username, password="Student@123456")
        )


def test_login_updates_last_login_time(db, student):
    assert student.last_login_time is None
    auth_services.login(db, LoginRequest(username=student.username, password="Student@123456"))
    db.refresh(student)
    assert student.last_login_time is not None


# ==================================================================
# 二、改密码
# ==================================================================
def test_change_password_requires_correct_old(db, student):
    with pytest.raises(PasswordIncorrectError):
        user_services.update_my_password(
            db,
            student,
            PasswordUpdate(old_password="wrong-old", new_password="NewPass@123"),
        )


def test_change_password_rejects_same_value(db, student):
    with pytest.raises(PasswordUnchangedError):
        user_services.update_my_password(
            db,
            student,
            PasswordUpdate(old_password="Student@123456", new_password="Student@123456"),
        )


def test_change_password_then_login_with_new(db, student):
    user_services.update_my_password(
        db, student, PasswordUpdate(old_password="Student@123456", new_password="NewPass@123")
    )
    result = auth_services.login(
        db, LoginRequest(username=student.username, password="NewPass@123")
    )
    assert result.token


def test_admin_reset_password_without_old(db, student):
    user_services.reset_password(db, student.id, PasswordReset(new_password="Reset@123456"))
    result = auth_services.login(
        db, LoginRequest(username=student.username, password="Reset@123456")
    )
    assert result.token


# ==================================================================
# 三、管理员自保护
# ==================================================================
def test_admin_cannot_demote_self(db, admin):
    with pytest.raises(UserSelfOperationError):
        user_services.admin_update_user(
            db, admin.id, UserUpdate(role=ROLE_STUDENT), operator=admin
        )


def test_admin_cannot_disable_self(db, admin):
    with pytest.raises(UserSelfOperationError):
        user_services.admin_update_user(
            db, admin.id, UserUpdate(status=USER_STATUS_DISABLED), operator=admin
        )


def test_admin_can_update_other_user(db, admin, student):
    result = user_services.admin_update_user(
        db, student.id, UserUpdate(name="张小明（已改名）"), operator=admin
    )
    assert result.name == "张小明（已改名）"


# ==================================================================
# 四、删除用户时的引用检查
# ==================================================================
def test_delete_user_with_reservation_is_blocked(db, admin, student):
    """还有预约的用户不能删：否则预约记录会变成孤儿数据"""
    lab = Lab(
        name=f"测试实验室-{uuid4().hex[:8]}",
        maxpeople=20,
        open_time=time(8, 0),
        close_time=time(22, 0),
        status=1,
    )
    db.add(lab)
    db.commit()
    db.refresh(lab)

    lab_services.create_reservation(
        db,
        ReservationCreate(
            lab_id=lab.id, reserve_start_time=at(14), reserve_end_time=at(16)
        ),
        user_id=student.id,
        user_name=student.name,
    )

    with pytest.raises(UserReferenceConflictError):
        user_services.delete_user(db, student.id, operator=admin)


def test_delete_plain_user_is_allowed(db, admin):
    plain = make_user(db, "20218888", name="无预约用户")
    user_services.delete_user(db, plain.id, operator=admin)
    assert db.get(type(plain), plain.id) is None


def test_admin_cannot_delete_self(db, admin):
    with pytest.raises(UserSelfOperationError):
        user_services.delete_user(db, admin.id, operator=admin)
