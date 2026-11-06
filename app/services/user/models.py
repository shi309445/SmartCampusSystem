"""用户业务数据模型（Pydantic）

只服务用户业务，所以放在业务模块内部 app/services/user/models.py；
全项目通用的数据模型（ORMBase / RequestBase / PageResponse）在 app/schemas/models.py。
用户表（User）是全校园共用的，放在 app/schemas/table.py。

约定：
    *_Create / *_Update  —— 上层（接口层 / Agent 工具）传入的请求模型
    *_Query              —— 条件查询模型
    *Response            —— 返回给上层的响应模型
"""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.models import ORMBase, PageResponse, RequestBase
from app.schemas.table import (
    ROLES,
    ROLE_STUDENT,
    USER_STATUS_NORMAL,
)
from app.utils.password import MAX_PASSWORD_BYTES, is_password_too_long

__all__ = [
    "PASSWORD_MIN_LENGTH",
    "PASSWORD_MAX_LENGTH",
    "UserCreate",
    "UserUpdate",
    "UserSelfUpdate",
    "UserQuery",
    "UserResponse",
    "PasswordUpdate",
    "PasswordReset",
    "LoginRequest",
    "LoginResponse",
    "RegisterRequest",
    "UserPage",
    "ORMBase",
    "PageResponse",
]

# 密码长度限制（按字符数）：bcrypt 的 72 字节上限由下面的字节校验兜底
PASSWORD_MIN_LENGTH = 6
PASSWORD_MAX_LENGTH = 64

# 账号长度限制
USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 50


def _strip_or_none(value: str | None) -> str | None:
    """去掉首尾空白；纯空白视作未填写（None），避免存入 "  " 这种脏数据"""
    if value is None:
        return None
    value = value.strip()
    return value or None


def _check_username_value(value: str) -> str:
    """账号校验：去空白、非空、内部不能有空白字符"""
    value = value.strip()
    if not value:
        raise ValueError("账号不能为空")
    if any(char.isspace() for char in value):
        raise ValueError("账号不能包含空白字符")
    return value


def _check_password_bytes(value: str) -> str:
    """密码字节校验：bcrypt 只处理前 72 字节

    中文字符占 3 个字节，24 个汉字就会超过 72 字节，只按字符数限制是挡不住的，
    这里提前拦住，避免在哈希阶段抛 ValueError 变成 500。
    """
    if is_password_too_long(value):
        raise ValueError(f"密码不能超过 {MAX_PASSWORD_BYTES} 字节（一个汉字约 3 字节）")
    return value


def _check_role(value: str) -> str:
    """角色校验：取值集中在 app/schemas/table.py 的 ROLES 里"""
    if value not in ROLES:
        raise ValueError(f"角色取值不合法，只能是: {', '.join(ROLES)}")
    return value


# ====================== 请求模型 ======================


class UserCreate(RequestBase):
    """管理员创建用户请求"""

    username: str = Field(
        ...,
        min_length=USERNAME_MIN_LENGTH,
        max_length=USERNAME_MAX_LENGTH,
        description="账号(学号/工号)，全库唯一",
    )
    password: str = Field(
        ...,
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description=f"密码，{PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} 个字符",
    )
    name: str = Field(..., min_length=1, max_length=50, description="姓名")
    role: str = Field(ROLE_STUDENT, description="角色: student-学生, admin-管理员")
    email: str | None = Field(None, max_length=255, description="邮箱")
    phone: str | None = Field(None, max_length=20, description="手机号")
    avatar: str | None = Field(None, max_length=500, description="头像地址")
    status: int = Field(USER_STATUS_NORMAL, ge=0, le=1, description="状态: 0-禁用, 1-正常")

    @field_validator("username")
    @classmethod
    def check_username(cls, v: str) -> str:
        return _check_username_value(v)

    @field_validator("password")
    @classmethod
    def check_password(cls, v: str) -> str:
        return _check_password_bytes(v)

    @field_validator("name")
    @classmethod
    def check_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("姓名不能为空")
        return v

    @field_validator("role")
    @classmethod
    def check_role(cls, v: str) -> str:
        return _check_role(v)

    @field_validator("email", "phone", "avatar")
    @classmethod
    def strip_optional_text(cls, v: str | None) -> str | None:
        return _strip_or_none(v)


class UserUpdate(RequestBase):
    """管理员更新用户请求（只传需要修改的字段）

    业务层用 exclude_unset 做增量更新，因此字段全部可选：
    不传 = 不改；显式传 null = 把 email / phone / avatar 清空。
    name / role / status 对应数据库的非空列，显式传 null 直接报参数错误
    （只在「传了」的时候才会触发这些校验，不传就是保持原值）。
    """

    name: str | None = Field(None, min_length=1, max_length=50, description="姓名")
    email: str | None = Field(None, max_length=255, description="邮箱")
    phone: str | None = Field(None, max_length=20, description="手机号")
    avatar: str | None = Field(None, max_length=500, description="头像地址")
    role: str | None = Field(None, description="角色: student-学生, admin-管理员")
    status: int | None = Field(None, ge=0, le=1, description="状态: 0-禁用, 1-正常")

    @field_validator("name")
    @classmethod
    def check_name(cls, v: str | None) -> str:
        if v is None or not v.strip():
            raise ValueError("姓名不能为空")
        return v.strip()

    @field_validator("role")
    @classmethod
    def check_role(cls, v: str | None) -> str:
        if v is None:
            raise ValueError("角色不能为空")
        return _check_role(v)

    @field_validator("status")
    @classmethod
    def check_status(cls, v: int | None) -> int:
        if v is None:
            raise ValueError("状态不能为空")
        return v

    @field_validator("email", "phone", "avatar")
    @classmethod
    def strip_optional_text(cls, v: str | None) -> str | None:
        return _strip_or_none(v)


class UserSelfUpdate(RequestBase):
    """用户修改自己的资料（没有角色 / 状态字段，从模型层面就改不了）"""

    name: str | None = Field(None, min_length=1, max_length=50, description="姓名")
    email: str | None = Field(None, max_length=255, description="邮箱")
    phone: str | None = Field(None, max_length=20, description="手机号")
    avatar: str | None = Field(None, max_length=500, description="头像地址")

    @field_validator("name")
    @classmethod
    def check_name(cls, v: str | None) -> str:
        if v is None or not v.strip():
            raise ValueError("姓名不能为空")
        return v.strip()

    @field_validator("email", "phone", "avatar")
    @classmethod
    def strip_optional_text(cls, v: str | None) -> str | None:
        return _strip_or_none(v)


class PasswordUpdate(RequestBase):
    """用户修改自己的密码请求（必须验证原密码）"""

    old_password: str = Field(
        ..., min_length=1, max_length=PASSWORD_MAX_LENGTH, description="原密码"
    )
    new_password: str = Field(
        ...,
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description=f"新密码，至少 {PASSWORD_MIN_LENGTH} 个字符",
    )

    @field_validator("old_password", "new_password")
    @classmethod
    def check_password(cls, v: str) -> str:
        return _check_password_bytes(v)


class PasswordReset(RequestBase):
    """管理员重置用户密码请求（不需要原密码）"""

    new_password: str = Field(
        ...,
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description=f"新密码，至少 {PASSWORD_MIN_LENGTH} 个字符",
    )

    @field_validator("new_password")
    @classmethod
    def check_password(cls, v: str) -> str:
        return _check_password_bytes(v)


class UserQuery(RequestBase):
    """用户条件查询模型（管理员用）"""

    keyword: str | None = Field(
        None, max_length=50, description="关键词，匹配账号、姓名、邮箱或手机号"
    )
    roles: list[str] | None = Field(
        None, description="角色列表，命中任意一个即可（多选为「或」关系）"
    )
    status: int | None = Field(None, ge=0, le=1, description="状态: 0-禁用, 1-正常")
    user_ids: list[int] | None = Field(None, description="用户ID列表，命中任意一个即可")

    @field_validator("roles")
    @classmethod
    def check_roles(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        for role in v:
            _check_role(role)
        return v


class RegisterRequest(RequestBase):
    """访客自助注册请求

    只允许注册成学生（role / status 由后端固定赋值），
    否则任何人都能注册出一个管理员账号。
    """

    username: str = Field(
        ...,
        min_length=USERNAME_MIN_LENGTH,
        max_length=USERNAME_MAX_LENGTH,
        description="账号(学号/工号)",
    )
    password: str = Field(
        ...,
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description=f"密码，至少 {PASSWORD_MIN_LENGTH} 个字符",
    )
    name: str | None = Field(None, min_length=1, max_length=50, description="姓名，默认与账号相同")

    @field_validator("username")
    @classmethod
    def check_username(cls, v: str) -> str:
        return _check_username_value(v)

    @field_validator("password")
    @classmethod
    def check_password(cls, v: str) -> str:
        return _check_password_bytes(v)

    @field_validator("name")
    @classmethod
    def check_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("姓名不能为空")
        return v


class LoginRequest(RequestBase):
    """登录请求"""

    username: str = Field(..., min_length=1, max_length=USERNAME_MAX_LENGTH, description="账号")
    password: str = Field(..., min_length=1, max_length=PASSWORD_MAX_LENGTH, description="密码")

    @field_validator("username")
    @classmethod
    def check_username(cls, v: str) -> str:
        return _check_username_value(v)


# ====================== 响应模型 ======================


class UserResponse(ORMBase):
    """用户信息响应（永远不含 password）"""

    id: int
    username: str
    name: str
    role: str
    email: str | None = None
    phone: str | None = None
    avatar: str | None = None
    status: int
    last_login_time: datetime | None = None
    create_time: datetime
    update_time: datetime


class LoginResponse(BaseModel):
    """登录响应：令牌 + 当前用户信息"""

    token: str = Field(..., description="访问令牌，后续请求放在 Authorization: Bearer <token>")
    token_type: str = Field("bearer", description="令牌类型，固定 bearer")
    expires_in: int = Field(..., description="令牌有效期(秒)")
    user: UserResponse = Field(..., description="当前登录用户")


# 分页响应别名（便于上层做类型标注与文档）
UserPage = PageResponse[UserResponse]
