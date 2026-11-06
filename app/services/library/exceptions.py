"""图书馆业务异常

只服务图书馆业务，因此放在业务模块内部（与 app/services/laboratory/exceptions.py 对齐）。
上层（FastAPI 全局异常处理器 / Agent 工具）按 code 转换即可：
    400 参数或业务规则错误, 403 无权限, 404 资源不存在, 409 资源冲突

上层如果已经有自己的业务异常基类，只需要让 LibraryException 继承它，
所有子类的转换逻辑不用改。
"""

from app.services.library.table import SeatReservation

__all__ = [
    "LibraryException",
    "RoomNotFoundError",
    "RoomClosedError",
    "RoomNameDuplicateError",
    "RoomCapacityExceededError",
    "SeatNotFoundError",
    "SeatCodeDuplicateError",
    "SeatUnavailableError",
    "ReservationNotFoundError",
    "ReservationUserNotFoundError",
    "ReservationTimeInvalidError",
    "ReservationOutsideOpenTimeError",
    "ReservationTimeConflictError",
    "ReservationUserConflictError",
    "ReservationStatusError",
    "ReservationPermissionError",
]


class LibraryException(Exception):
    """图书馆业务异常基类

    code 同时作为 HTTP 状态码使用。
    """

    def __init__(self, message: str, code: int = 400) -> None:
        self.message = message
        self.code = code
        super().__init__(message)

    def __str__(self) -> str:  # pragma: no cover - 便于日志排查
        return f"[{self.code}] {self.message}"


# ====================== 自习室 ======================


class RoomNotFoundError(LibraryException):
    """自习室不存在（404）"""

    def __init__(self, room_id: int) -> None:
        self.room_id = room_id
        super().__init__("自习室不存在", code=404)


class RoomClosedError(LibraryException):
    """自习室已关闭（409）"""

    def __init__(self, room_id: int) -> None:
        self.room_id = room_id
        super().__init__("自习室已关闭", code=409)


class RoomNameDuplicateError(LibraryException):
    """自习室名称重复（409）"""

    def __init__(self, name: str = "") -> None:
        self.name = name
        super().__init__("自习室名称已存在", code=409)


class RoomCapacityExceededError(LibraryException):
    """座位数超过自习室容量上限（400）

    自习室的 capacity 就是座位数上限，保证「座位数 = 可容纳人数」，
    这样按座位预约时天然不会超过容量。
    """

    def __init__(self, capacity: int, current: int, adding: int) -> None:
        self.capacity = capacity
        self.current = current
        self.adding = adding
        super().__init__(
            f"座位数超过自习室容量上限（上限 {capacity}，已有 {current}，本次新增 {adding}）",
            code=400,
        )


# ====================== 座位 ======================


class SeatNotFoundError(LibraryException):
    """座位不存在（404）"""

    def __init__(self, seat_id: int) -> None:
        self.seat_id = seat_id
        super().__init__("座位不存在", code=404)


class SeatCodeDuplicateError(LibraryException):
    """座位编号重复（409）"""

    def __init__(self, code: str = "") -> None:
        self.code = code
        message = f"座位编号重复: {code}" if code else "座位编号重复"
        super().__init__(message, code=409)


class SeatUnavailableError(LibraryException):
    """座位当前不可用（409）：座位被停用，或所属自习室已关闭"""

    def __init__(self, message: str = "该座位当前不可用", code: int = 409) -> None:
        super().__init__(message, code=code)


# ====================== 预约 ======================


class ReservationNotFoundError(LibraryException):
    """预约不存在（404）"""

    def __init__(self, reservation_id: int) -> None:
        self.reservation_id = reservation_id
        super().__init__("预约不存在", code=404)


class ReservationUserNotFoundError(LibraryException):
    """预约要关联的用户不存在（404）

    library_reservations.user_id 有指向 users 的外键，数据库会兜底拦下脏数据；
    这里提前查一次是为了把 IntegrityError 换成一句能看懂的提示。
    handler_id 是历史快照（故意没加外键），同样在写入前校验。
    """

    def __init__(self, user_id: int, label: str = "用户") -> None:
        self.user_id = user_id
        self.label = label
        super().__init__(f"{label}不存在: {user_id}", code=404)


class ReservationTimeInvalidError(LibraryException):
    """预约时间不合法（400）：非同一天 / 已过期 / 超出可提前天数 / 超过单次时长上限"""

    def __init__(self, message: str = "预约时间不合法") -> None:
        super().__init__(message, code=400)


class ReservationOutsideOpenTimeError(LibraryException):
    """预约时间不在自习室开放时间内（400）"""

    def __init__(self, message: str = "预约时间不在开放时间内") -> None:
        super().__init__(message, code=400)


class ReservationTimeConflictError(LibraryException):
    """座位在该时段已被占用（409）

    conflicts 保留冲突明细（ORM 对象），供调用方提示「被哪个单号占了」。
    明细在构造时就会快照一份，因此业务层回滚事务（释放行锁）之后依然可以读取。
    """

    def __init__(self, conflicts: list[SeatReservation] | None = None) -> None:
        self.conflicts = list(conflicts or [])
        self._details = [
            {
                "reservation_no": item.reservation_no,
                "seat_code": item.seat_code,
                "start_time": item.reserve_start_time,
                "end_time": item.reserve_end_time,
            }
            for item in self.conflicts
        ]
        super().__init__("该座位在所选时段已被预约，请换个座位或时间", code=409)

    def conflict_details(self) -> list[dict]:
        """冲突明细：只暴露单号 / 座位号 / 时段，避免泄露他人的 user_id、备注等隐私信息"""
        return [dict(item) for item in self._details]


class ReservationUserConflictError(LibraryException):
    """申请人自己在该时段已经有别的预约（409）

    自习室是「一人一座」：同一个人同一时段只能有一个有效预约，
    否则会出现一个人同时占住两个座位的情况。
    """

    def __init__(self, message: str = "您在该时段已有其它座位预约，请先取消后再预约") -> None:
        super().__init__(message, code=409)


class ReservationStatusError(LibraryException):
    """当前预约状态不允许该操作（默认 409）"""

    def __init__(self, message: str = "当前预约状态不允许该操作", code: int = 409) -> None:
        super().__init__(message, code=code)


class ReservationPermissionError(LibraryException):
    """无权操作他人的预约（403）"""

    def __init__(self, message: str = "无权操作该预约") -> None:
        super().__init__(message, code=403)
