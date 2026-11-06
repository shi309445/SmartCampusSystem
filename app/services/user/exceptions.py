"""用户业务异常

只服务用户业务，因此放在业务模块内部。
上层（FastAPI 全局异常处理器 / Agent 工具）按 code 转换即可：
    400 参数或业务规则错误, 401 未认证, 403 无权限, 404 资源不存在, 409 资源冲突

上层如果已经有自己的业务异常基类，只需要让 UserException 继承它，
所有子类的转换逻辑不用改。
"""

__all__ = [
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
]


class UserException(Exception):
    """用户业务异常基类

    code 同时作为 HTTP 状态码使用。
    """

    def __init__(self, message: str, code: int = 400) -> None:
        self.message = message
        self.code = code
        super().__init__(message)

    def __str__(self) -> str:  # pragma: no cover - 便于日志排查
        return f"[{self.code}] {self.message}"


class UserNotFoundError(UserException):
    """用户不存在（404）"""

    def __init__(self, user_id: int | None = None) -> None:
        self.user_id = user_id
        message = f"用户不存在: {user_id}" if user_id is not None else "用户不存在"
        super().__init__(message, code=404)


class UsernameDuplicateError(UserException):
    """账号已存在（409）"""

    def __init__(self, username: str = "") -> None:
        self.username = username
        message = f"账号已存在: {username}" if username else "账号已存在"
        super().__init__(message, code=409)


class UserAuthenticationError(UserException):
    """账号或密码错误（401）

    账号不存在与密码错误必须返回同一句提示，避免被用来枚举系统里有哪些账号。
    """

    def __init__(self, message: str = "账号或密码错误") -> None:
        super().__init__(message, code=401)


class TokenInvalidError(UserException):
    """登录令牌无效 / 已过期（401）"""

    def __init__(self, message: str = "登录已过期，请重新登录") -> None:
        super().__init__(message, code=401)


class UserDisabledError(UserException):
    """账号已被禁用（403）"""

    def __init__(self, message: str = "账号已被禁用，请联系管理员") -> None:
        super().__init__(message, code=403)


class UserPermissionError(UserException):
    """无权限执行该操作（403）"""

    def __init__(self, message: str = "无权限执行该操作") -> None:
        super().__init__(message, code=403)


class UserSelfOperationError(UserException):
    """不允许对当前登录账号执行该操作（409）

    典型场景：管理员把自己降级 / 禁用 / 删除，会让系统失去管理入口。
    """

    def __init__(self, message: str = "不能对当前登录的账号执行该操作") -> None:
        super().__init__(message, code=409)


class PasswordIncorrectError(UserException):
    """原密码错误（400）"""

    def __init__(self, message: str = "原密码错误") -> None:
        super().__init__(message, code=400)


class PasswordUnchangedError(UserException):
    """新密码与原密码相同（400）"""

    def __init__(self, message: str = "新密码不能与原密码相同") -> None:
        super().__init__(message, code=400)


class UserReferenceConflictError(UserException):
    """用户存在关联业务数据，不允许删除（409）"""

    def __init__(self, message: str = "该用户存在关联业务数据，无法删除") -> None:
        super().__init__(message, code=409)
