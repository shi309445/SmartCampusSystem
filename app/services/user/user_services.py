"""用户业务最上层逻辑：用户增删改查 / 个人资料 / 密码 / 角色与状态

本文件是用户模块的账号管理入口，覆盖：
    查询：详情、条件列表、分页、管理员数量统计
    个人：查看自己的信息、修改资料、修改密码
    管理员：创建用户、更新（角色 / 状态 / 资料）、重置密码、删除用户、初始化首个管理员

几个关键业务规则：
    1. 密码只存 bcrypt 哈希，任何响应模型里都不含 password 字段。
    2. 用户自己改资料只能动 name / email / phone / avatar（UserSelfUpdate 里根本没有
       role / status 字段，模型层就改不了），角色与状态只有管理员接口能改。
    3. 不允许管理员把自己降级 / 禁用 / 删除，避免系统失去管理入口。
    4. 存在关联业务数据（如实验室预约）的用户不允许删除，见 _check_user_references_or_raise。

事务约定：每个函数自己 commit；写入失败先回滚 Session 再抛 UserException 子类，
所以上层捕获异常后可以安全地复用同一个 Session。
Session 的创建与关闭由调用方（接口层 / Agent 工具）负责。

鉴权边界：本文件不校验 operator 是否真的是管理员（由上层鉴权负责，
见 app/services/user/auth_services.py 的 check_admin_or_raise），
只做「与操作人自身相关」的保护。
注册 / 登录 / 令牌校验在 auth_services.py。
"""

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.schemas.table import (
    ROLES,
    ROLE_ADMIN,
    USER_STATUS_NORMAL,
    User,
)
from app.services.user.exceptions import (
    PasswordIncorrectError,
    PasswordUnchangedError,
    UsernameDuplicateError,
    UserException,
    UserNotFoundError,
    UserReferenceConflictError,
    UserSelfOperationError,
)
from app.services.user.models import (
    PasswordReset,
    PasswordUpdate,
    UserCreate,
    UserPage,
    UserQuery,
    UserResponse,
    UserSelfUpdate,
    UserUpdate,
)
from app.utils.password import hash_password, is_same_password, verify_password

__all__ = [
    "MAX_PAGE_SIZE",
    "SELF_MUTABLE_FIELDS",
    "ADMIN_MUTABLE_FIELDS",
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

# 分页上限：防止上层传入超大 page_size 把库拖垮
MAX_PAGE_SIZE = 100

# 用户自己可以改的字段（角色 / 状态 / 密码不在其中）
SELF_MUTABLE_FIELDS = ("name", "email", "phone", "avatar")
# 管理员可以改的字段（账号 username 与密码 password 都不在这里：
# 账号是登录凭据不允许改名，密码只能走「本人改密码」或「管理员重置密码」）
ADMIN_MUTABLE_FIELDS = ("name", "email", "phone", "avatar", "role", "status")


# ==================================================================
# 通用小工具
# ==================================================================
def _escape_like(keyword: str) -> str:
    """转义 LIKE 通配符，避免用户输入的 % / _ 变成通配符"""
    return keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _normalize_page(page: int, page_size: int) -> tuple[int, int]:
    """收敛分页参数：页码至少 1，每页条数限制在 1 ~ MAX_PAGE_SIZE"""
    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or 10), 1), MAX_PAGE_SIZE)
    return page, page_size


# ==================================================================
# 一、查询
# ==================================================================
def get_user_or_raise(db: Session, user_id: int) -> User:
    """获取用户，不存在则抛 UserNotFoundError"""
    user = db.get(User, user_id)
    if user is None:
        raise UserNotFoundError(user_id)
    return user


def get_user_info(user: User) -> UserResponse:
    """把当前登录用户转成响应模型（上层拿到 User 后展示用）"""
    return UserResponse.model_validate(user)


def get_user_detail(db: Session, user_id: int) -> UserResponse:
    """查询指定用户详情（管理员视角）"""
    return UserResponse.model_validate(get_user_or_raise(db, user_id))


def check_username_unique_or_raise(
    db: Session, username: str, exclude_id: int | None = None
) -> None:
    """账号查重

    exclude_id: 更新场景传入自身 id，避免把自己查出来误判为重复。
    数据库层面 username 上也有唯一索引兜底。
    """
    query = db.query(User.id).filter(User.username == username)
    if exclude_id is not None:
        query = query.filter(User.id != exclude_id)
    if query.first() is not None:
        raise UsernameDuplicateError(username)


def _build_user_query(data: UserQuery) -> Select:
    """根据条件构造用户查询语句"""
    query = select(User)

    if data.user_ids:
        user_ids = [user_id for user_id in data.user_ids if user_id > 0]
        if user_ids:
            query = query.where(User.id.in_(user_ids))

    if data.roles:
        roles = [role for role in data.roles if role in ROLES]
        if roles:
            query = query.where(User.role.in_(roles))

    if data.status is not None:
        query = query.where(User.status == data.status)

    if data.keyword:
        like = f"%{_escape_like(data.keyword)}%"
        query = query.where(
            or_(
                User.username.ilike(like, escape="\\"),
                User.name.ilike(like, escape="\\"),
                User.email.ilike(like, escape="\\"),
                User.phone.ilike(like, escape="\\"),
            )
        )

    return query


def list_users(
    db: Session,
    *,
    keyword: str | None = None,
    roles: list[str] | None = None,
    status: int | None = None,
) -> list[UserResponse]:
    """条件查询用户列表（不分页）"""
    data = UserQuery(keyword=keyword, roles=roles, status=status)
    users = db.scalars(_build_user_query(data).order_by(User.id.desc())).all()
    return [UserResponse.model_validate(user) for user in users]


def get_user_page(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 10,
    keyword: str | None = None,
    roles: list[str] | None = None,
    status: int | None = None,
) -> UserPage:
    """分页查询用户（管理员用户列表）"""
    page, page_size = _normalize_page(page, page_size)
    data = UserQuery(keyword=keyword, roles=roles, status=status)
    query = _build_user_query(data)

    total = int(db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    users = db.scalars(
        query.order_by(User.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()

    return UserPage(
        total=total,
        page=page,
        page_size=page_size,
        data=[UserResponse.model_validate(user) for user in users],
    )


def count_admins(db: Session, *, only_active: bool = False) -> int:
    """统计管理员数量

    only_active=True 时只统计状态正常的管理员，用于判断「系统还有没有可用的管理入口」。
    """
    query = db.query(func.count(User.id)).filter(User.role == ROLE_ADMIN)
    if only_active:
        query = query.filter(User.status == USER_STATUS_NORMAL)
    return int(query.scalar() or 0)


# ==================================================================
# 二、创建用户（管理员 / 初始化）
# ==================================================================
def _create_user_row(
    db: Session,
    *,
    username: str,
    password: str,
    name: str,
    role: str,
    email: str | None = None,
    phone: str | None = None,
    avatar: str | None = None,
    status: int = USER_STATUS_NORMAL,
) -> UserResponse:
    """用户落库的公共实现：查重 -> 哈希密码 -> 入库

    管理员创建与自助注册都走这里，保证两条路径的密码处理完全一致。
    """
    check_username_unique_or_raise(db, username)

    user = User(
        username=username,
        password=hash_password(password),
        name=name,
        role=role,
        email=email,
        phone=phone,
        avatar=avatar,
        status=status,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # 唯一索引兜底：并发注册同一账号时先查重也会漏，交给数据库报错更安全
        db.rollback()
        raise UsernameDuplicateError(username)
    db.refresh(user)
    return UserResponse.model_validate(user)


def create_user(db: Session, data: UserCreate) -> UserResponse:
    """管理员创建用户（可以指定角色与状态）"""
    return _create_user_row(
        db,
        username=data.username,
        password=data.password,
        name=data.name,
        role=data.role,
        email=data.email,
        phone=data.phone,
        avatar=data.avatar,
        status=data.status,
    )


def ensure_initial_admin(
    db: Session,
    *,
    username: str,
    password: str,
    name: str | None = None,
) -> tuple[UserResponse, bool]:
    """初始化第一个管理员（首次部署 / 测试用），返回 (管理员, 本次是否新建)

    幂等：库里已经存在任何管理员时直接返回最早的那个，不会重复创建、也不会改它的密码。
    账号密码必须由调用方显式传入，代码里不留任何默认口令。
    """
    existing = (
        db.query(User).filter(User.role == ROLE_ADMIN).order_by(User.id).first()
    )
    if existing is not None:
        return UserResponse.model_validate(existing), False

    created = _create_user_row(
        db,
        username=username,
        password=password,
        name=name or username,
        role=ROLE_ADMIN,
    )
    return created, True


# ==================================================================
# 三、用户操作自己的账号
# ==================================================================
def update_my_info(db: Session, user: User, data: UserSelfUpdate) -> UserResponse:
    """修改当前用户自己的资料（角色 / 状态改不了）"""
    payload = data.model_dump(exclude_unset=True)
    for field, value in payload.items():
        if field in SELF_MUTABLE_FIELDS:
            setattr(user, field, value)

    db.commit()
    db.refresh(user)
    return UserResponse.model_validate(user)


def update_my_password(db: Session, user: User, data: PasswordUpdate) -> None:
    """修改当前用户自己的密码（必须验证原密码）"""
    if not verify_password(data.old_password, user.password):
        raise PasswordIncorrectError()
    if is_same_password(data.new_password, user.password):
        raise PasswordUnchangedError()

    user.password = hash_password(data.new_password)
    db.commit()


# ==================================================================
# 四、管理员操作用户
# ==================================================================
def admin_update_user(
    db: Session, user_id: int, data: UserUpdate, *, operator: User
) -> UserResponse:
    """管理员更新用户资料 / 角色 / 状态

    不允许管理员把自己降级或禁用，避免把系统的管理入口关掉。
    """
    user = get_user_or_raise(db, user_id)
    payload = data.model_dump(exclude_unset=True)

    if user.id == operator.id:
        if "role" in payload and payload["role"] != ROLE_ADMIN:
            raise UserSelfOperationError("不能修改自己的角色")
        if "status" in payload and payload["status"] != USER_STATUS_NORMAL:
            raise UserSelfOperationError("不能禁用自己的账号")

    for field, value in payload.items():
        if field in ADMIN_MUTABLE_FIELDS:
            setattr(user, field, value)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise UserException("用户信息更新失败", code=409)
    db.refresh(user)
    return UserResponse.model_validate(user)


def reset_password(db: Session, user_id: int, data: PasswordReset) -> None:
    """管理员重置用户密码（不需要原密码，用于学生忘记密码的场景）"""
    user = get_user_or_raise(db, user_id)

    if is_same_password(data.new_password, user.password):
        raise PasswordUnchangedError()

    user.password = hash_password(data.new_password)
    db.commit()


def _check_user_references_or_raise(db: Session, user_id: int) -> None:
    """删除用户前的跨模块引用检查

    其它业务模块保存了 user_id（用户表是全项目通用的，但业务表之间不建外键，
    存的是冗余快照），用户被删掉后这些记录会变成孤儿数据，所以在这里主动拦一道。
    新增业务模块如果也记录了 user_id，在这里补一个检查即可。
    """
    # 局部导入，让「用户模块依赖哪些业务模块」一眼可见
    from app.services.laboratory.table import LabReservation
    from app.services.library.table import SeatReservation

    has_lab_reservation = (
        db.query(LabReservation.id)
        .filter(LabReservation.user_id == user_id)
        .first()
        is not None
    )
    if has_lab_reservation:
        raise UserReferenceConflictError("该用户存在实验室预约记录，无法删除")

    has_seat_reservation = (
        db.query(SeatReservation.id)
        .filter(SeatReservation.user_id == user_id)
        .first()
        is not None
    )
    if has_seat_reservation:
        raise UserReferenceConflictError("该用户存在图书馆座位预约记录，无法删除")


def delete_user(db: Session, user_id: int, *, operator: User) -> None:
    """管理员删除用户

    不能删除当前登录账号；存在关联业务数据的用户也不能删除。
    """
    if user_id == operator.id:
        raise UserSelfOperationError("不能删除当前登录的账号")

    user = get_user_or_raise(db, user_id)
    _check_user_references_or_raise(db, user_id)

    try:
        db.delete(user)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise UserReferenceConflictError()
