"""用户鉴权业务：注册 / 登录 / 令牌换用户

与 user_services.py 的分工：
    auth_services  面向「还没登录的访客」：注册、登录、拿 token 换当前用户
    user_services  面向「已经登录的人」：资料、密码、角色与状态的增删改查

鉴权流程：
    1. 登录成功后签发 JWT（payload 里只有 user_id）
    2. 之后每个请求都带上令牌，get_current_user_or_raise() 解析令牌并回库查用户
    3. 每次请求都回库查，所以管理员改了角色或禁用了账号能立刻生效，不用等令牌过期

JWT 是「无状态」令牌：服务端不记录会话，所以退出登录由前端丢弃令牌即可
（如果需要「立即失效」的黑名单能力，那是后续独立的会话管理功能）。
"""

from datetime import datetime

from jwt import PyJWTError
from sqlalchemy.orm import Session

from app.schemas.table import (
    ROLE_ADMIN,
    ROLE_STUDENT,
    USER_STATUS_NORMAL,
    User,
)
from app.services.user.exceptions import (
    TokenInvalidError,
    UserAuthenticationError,
    UserDisabledError,
    UserPermissionError,
)
from app.services.user.models import (
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    UserCreate,
    UserResponse,
)
from app.services.user.user_services import create_user
from app.utils.jwt import (
    access_token_ttl_seconds,
    create_access_token,
    decode_access_token,
)
from app.utils.password import verify_password

__all__ = [
    "register",
    "login",
    "get_current_user_or_raise",
    "get_current_user_info",
    "check_admin_or_raise",
    "require_admin",
    "decode_user_id",
]


# ==================================================================
# 一、注册 / 登录
# ==================================================================
def register(db: Session, data: RegisterRequest) -> UserResponse:
    """访客自助注册

    角色固定为 student、状态固定为正常：注册接口不接受角色参数，
    否则任何人都能注册出一个管理员账号（管理员由管理员创建）。
    """
    return create_user(
        db,
        UserCreate(
            username=data.username,
            password=data.password,
            name=data.name or data.username,
            role=ROLE_STUDENT,
            status=USER_STATUS_NORMAL,
        ),
    )


def login(db: Session, data: LoginRequest) -> LoginResponse:
    """账号密码登录，返回令牌与当前用户信息"""
    user = db.query(User).filter(User.username == data.username).first()

    # 账号不存在与密码错误返回同一句提示，避免被用来枚举系统里有哪些账号。
    # verify_password 内部已兜底非法哈希 / 超长密码，只会返回 True/False。
    if user is None or not verify_password(data.password, user.password):
        raise UserAuthenticationError()

    # 密码正确后再判断启用状态：不然「账号被禁用」会变成一个免密探测接口
    if user.status != USER_STATUS_NORMAL:
        raise UserDisabledError()

    # 记录最近登录时间（顺带完成一次写操作，便于管理员排查僵尸账号）
    user.last_login_time = datetime.now()
    db.commit()
    db.refresh(user)

    return LoginResponse(
        token=create_access_token(user.id),
        expires_in=access_token_ttl_seconds(),
        user=UserResponse.model_validate(user),
    )


# ==================================================================
# 二、令牌 -> 当前用户
# ==================================================================
def decode_user_id(token: str) -> int:
    """解析令牌取出 user_id，任何异常都转成 TokenInvalidError(401)"""
    try:
        payload = decode_access_token(token)
    except PyJWTError:
        # 签名不对 / 已过期 / 缺 exp：对使用者来说都是「重新登录」
        raise TokenInvalidError("登录已过期或令牌无效，请重新登录")

    user_id = payload.get("user_id")
    if not isinstance(user_id, int):
        raise TokenInvalidError("无效的登录凭证")
    return user_id


def get_current_user_or_raise(db: Session, token: str) -> User:
    """把登录令牌换成当前用户（上层把它包成 FastAPI 依赖 / Agent 上下文）

    用户可能已被删除或被禁用，两种情况都要在业务层拦住，
    否则会拿着一个「已经不存在的用户」继续跑业务。
    """
    user = db.get(User, decode_user_id(token))
    if user is None:
        raise TokenInvalidError("登录已失效，请重新登录")
    if user.status != USER_STATUS_NORMAL:
        raise UserDisabledError()
    return user


def get_current_user_info(db: Session, token: str) -> UserResponse:
    """把登录令牌换成当前用户信息（只需要展示时的便捷方法）"""
    return UserResponse.model_validate(get_current_user_or_raise(db, token))


# ==================================================================
# 三、权限校验
# ==================================================================
def check_admin_or_raise(user: User) -> User:
    """要求当前用户是管理员，否则抛 UserPermissionError(403)"""
    if user.role != ROLE_ADMIN:
        raise UserPermissionError("需要管理员权限")
    return user


# 语义化别名，上层写 `user = require_admin(current)` 更直观
require_admin = check_admin_or_raise
