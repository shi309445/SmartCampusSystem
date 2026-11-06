"""用户业务模块（app/services/user）

目录结构：
    models.py        用户业务数据模型（请求 / 响应 / 查询）
    exceptions.py    用户业务异常（按 code 映射 HTTP 状态码）
    user_services.py 账号管理：增删改查、个人资料、密码、角色与状态
    auth_services.py 鉴权：注册、登录、令牌换当前用户、管理员校验

用户表（users）是全校园共用的主体，按「通用表放 app/schemas」的约定放在
app/schemas/table.py；密码哈希与 JWT 是通用工具，放在 app/utils/ 下。

用法示例：
    from app.database import SessionLocal
    from app.services.user import auth_services, user_services
    from app.services.user.models import LoginRequest

    with SessionLocal() as db:
        # 首次部署：初始化一个管理员（已存在管理员时直接返回，不会重复创建）
        admin, created = user_services.ensure_initial_admin(
            db, username="admin", password="admin123456", name="系统管理员"
        )
        # 登录
        result = auth_services.login(
            db, LoginRequest(username="admin", password="admin123456")
        )
        # 用令牌换当前用户
        current = auth_services.get_current_user_or_raise(db, result.token)
"""

from app.schemas.table import (
    ROLES,
    ROLE_ADMIN,
    ROLE_STUDENT,
    USER_STATUS_DISABLED,
    USER_STATUS_NORMAL,
    USER_STATUSES,
)
from app.services.user import auth_services, exceptions, models, user_services
from app.services.user.auth_services import (
    check_admin_or_raise,
    decode_user_id,
    get_current_user_info,
    get_current_user_or_raise,
    login,
    register,
    require_admin,
)
from app.services.user.exceptions import (
    PasswordIncorrectError,
    PasswordUnchangedError,
    TokenInvalidError,
    UserAuthenticationError,
    UserDisabledError,
    UserException,
    UsernameDuplicateError,
    UserNotFoundError,
    UserPermissionError,
    UserReferenceConflictError,
    UserSelfOperationError,
)
from app.services.user.models import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    LoginRequest,
    LoginResponse,
    PasswordReset,
    PasswordUpdate,
    RegisterRequest,
    UserCreate,
    UserPage,
    UserQuery,
    UserResponse,
    UserSelfUpdate,
    UserUpdate,
)
from app.services.user.user_services import (
    admin_update_user,
    count_admins,
    create_user,
    delete_user,
    ensure_initial_admin,
    get_user_detail,
    get_user_info,
    get_user_or_raise,
    get_user_page,
    list_users,
    reset_password,
    update_my_info,
    update_my_password,
)

__all__ = [
    # 子模块
    "auth_services",
    "exceptions",
    "models",
    "user_services",
    # 异常
    "UserException",
    "UserNotFoundError",
    "UsernameDuplicateError",
    "UserAuthenticationError",
    "TokenInvalidError",
    "UserDisabledError",
    "UserPermissionError",
    "UserSelfOperationError",
    "PasswordIncorrectError",
    "PasswordUnchangedError",
    "UserReferenceConflictError",
    # 角色与状态常量（取值集中在 app/schemas/table.py）
    "ROLE_STUDENT",
    "ROLE_ADMIN",
    "ROLES",
    "USER_STATUS_DISABLED",
    "USER_STATUS_NORMAL",
    "USER_STATUSES",
    # 数据模型
    "PASSWORD_MIN_LENGTH",
    "PASSWORD_MAX_LENGTH",
    "UserCreate",
    "UserUpdate",
    "UserSelfUpdate",
    "UserQuery",
    "UserResponse",
    "UserPage",
    "PasswordUpdate",
    "PasswordReset",
    "RegisterRequest",
    "LoginRequest",
    "LoginResponse",
    # 鉴权
    "register",
    "login",
    "get_current_user_or_raise",
    "get_current_user_info",
    "decode_user_id",
    "check_admin_or_raise",
    "require_admin",
    # 账号管理
    "get_user_or_raise",
    "get_user_info",
    "get_user_detail",
    "list_users",
    "get_user_page",
    "count_admins",
    "create_user",
    "update_my_info",
    "update_my_password",
    "admin_update_user",
    "reset_password",
    "delete_user",
    "ensure_initial_admin",
]
