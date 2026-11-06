"""实验室业务异常

只服务实验室业务，因此放在业务模块内部。
上层（FastAPI 全局异常处理器 / Agent 工具）按 code 转换即可：
    400 参数或业务规则错误, 403 无权限, 404 资源不存在, 409 资源冲突

上层如果已经有自己的业务异常基类，只需要让 LaboratoryException 继承它，
所有子类的转换逻辑不用改。
"""

from app.services.laboratory.table import LabReservation

__all__ = [
    "LaboratoryException",
    "LabNotFoundError",
    "LabClosedError",
    "LabNameDuplicateError",
    "EquipmentNotFoundError",
    "EquipmentCodeDuplicateError",
    "ReservationNotFoundError",
    "ReservationUserNotFoundError",
    "ReservationTimeInvalidError",
    "ReservationOutsideOpenTimeError",
    "ReservationCapacityError",
    "ReservationTimeConflictError",
    "ReservationStatusError",
    "ReservationPermissionError",
]


class LaboratoryException(Exception):
    """实验室业务异常基类

    code 同时作为 HTTP 状态码使用。
    """

    def __init__(self, message: str, code: int = 400) -> None:
        self.message = message
        self.code = code
        super().__init__(message)

    def __str__(self) -> str:  # pragma: no cover - 便于日志排查
        return f"[{self.code}] {self.message}"


# ====================== 实验室 ======================


class LabNotFoundError(LaboratoryException):
    """实验室不存在（404）"""

    def __init__(self, lab_id: int) -> None:
        self.lab_id = lab_id
        super().__init__("实验室不存在", code=404)


class LabClosedError(LaboratoryException):
    """实验室已关闭（409）"""

    def __init__(self, lab_id: int) -> None:
        self.lab_id = lab_id
        super().__init__("实验室已关闭", code=409)


class LabNameDuplicateError(LaboratoryException):
    """实验室名称重复（409）"""

    def __init__(self, name: str = "") -> None:
        self.name = name
        super().__init__("实验室名称已存在", code=409)


# ====================== 实验室设备 ======================


class EquipmentNotFoundError(LaboratoryException):
    """实验设备不存在（404）"""

    def __init__(self, equipment_id: int) -> None:
        self.equipment_id = equipment_id
        super().__init__("实验设备不存在", code=404)


class EquipmentCodeDuplicateError(LaboratoryException):
    """设备编号重复（409）"""

    def __init__(self, code: str = "") -> None:
        self.code = code
        message = f"设备编号重复: {code}" if code else "设备编号重复"
        super().__init__(message, code=409)


# ====================== 预约 ======================


class ReservationNotFoundError(LaboratoryException):
    """预约不存在（404）"""

    def __init__(self, reservation_id: int) -> None:
        self.reservation_id = reservation_id
        super().__init__("预约不存在", code=404)


class ReservationUserNotFoundError(LaboratoryException):
    """预约要关联的用户不存在（404）

    lab_reservations.user_id 有指向 users 的外键，数据库会兜底拦下脏数据；
    这里提前查一次是为了把 IntegrityError 换成一句能看懂的提示。
    handler_id 是历史快照（故意没加外键），同样在写入前校验，避免留下悬空的处理人。
    """

    def __init__(self, user_id: int, label: str = "用户") -> None:
        self.user_id = user_id
        self.label = label
        super().__init__(f"{label}不存在: {user_id}", code=404)


class ReservationTimeInvalidError(LaboratoryException):
    """预约时间不合法（400）：非同一天 / 已过期 / 超出可提前预约天数"""

    def __init__(self, message: str = "预约时间不合法") -> None:
        super().__init__(message, code=400)


class ReservationOutsideOpenTimeError(LaboratoryException):
    """预约时间不在实验室开放时间内（400）"""

    def __init__(self, message: str = "预约时间不在开放时间内") -> None:
        super().__init__(message, code=400)


class ReservationCapacityError(LaboratoryException):
    """预约人数超过实验室容量（400）"""

    def __init__(self, people_num: int, maxpeople: int) -> None:
        self.people_num = people_num
        self.maxpeople = maxpeople
        super().__init__(
            f"使用人数({people_num})超过实验室最大容纳人数({maxpeople})", code=400
        )


class ReservationTimeConflictError(LaboratoryException):
    """预约时段冲突（409）

    conflicts 保留冲突明细（ORM 对象），供调用方提示「该时段被哪些预约占用了」。
    明细在构造时就会快照一份，因此业务层回滚事务（releasing 行锁）之后依然可以读取。
    """

    def __init__(self, conflicts: list[LabReservation] | None = None) -> None:
        self.conflicts = list(conflicts or [])
        self._details = [
            {
                "reservation_no": item.reservation_no,
                "start_time": item.reserve_start_time,
                "end_time": item.reserve_end_time,
            }
            for item in self.conflicts
        ]
        super().__init__("该时段已被预约，请选择其它时间", code=409)

    def conflict_details(self) -> list[dict]:
        """冲突明细：只暴露单号与时段，避免泄露他人的 user_id / 备注等隐私信息"""
        return [dict(item) for item in self._details]


class ReservationStatusError(LaboratoryException):
    """当前预约状态不允许该操作（默认 409）"""

    def __init__(self, message: str = "当前预约状态不允许该操作", code: int = 409) -> None:
        super().__init__(message, code=code)


class ReservationPermissionError(LaboratoryException):
    """无权操作他人的预约（403）"""

    def __init__(self, message: str = "无权操作该预约") -> None:
        super().__init__(message, code=403)
