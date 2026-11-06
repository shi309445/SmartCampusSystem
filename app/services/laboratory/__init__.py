"""实验室业务模块（app/services/laboratory）

目录结构：
    table.py        实验室业务专用表: labs / lab_equipments / lab_reservations
    models.py       实验室业务数据模型（请求 / 响应 / 查询）
    exceptions.py   实验室业务异常（按 code 映射 HTTP 状态码）
    lab_services.py 最上层业务逻辑：实验室增删改查、设备增删改查、预约与审核

全项目通用的表和数据模型在 app/schemas/table.py 与 app/schemas/models.py。

用法示例：
    from app.database import SessionLocal
    from app.services.laboratory import lab_services
    from app.services.laboratory.models import LabCreate

    with SessionLocal() as db:
        lab = lab_services.create_lab(db, LabCreate(name="物理实验室", maxpeople=30))
"""

from app.services.laboratory import exceptions, models, table
from app.services.laboratory.exceptions import (
    EquipmentCodeDuplicateError,
    EquipmentNotFoundError,
    LabClosedError,
    LabNameDuplicateError,
    LaboratoryException,
    LabNotFoundError,
    ReservationCapacityError,
    ReservationNotFoundError,
    ReservationOutsideOpenTimeError,
    ReservationPermissionError,
    ReservationStatusError,
    ReservationTimeConflictError,
    ReservationTimeInvalidError,
    ReservationUserNotFoundError,
)
from app.services.laboratory.lab_services import *  # noqa: F403
from app.services.laboratory.lab_services import __all__ as _service_exports
from app.services.laboratory.table import (
    OCCUPYING_STATUSES,
    RESERVATION_STATUS_APPROVED,
    RESERVATION_STATUS_CANCELLED,
    RESERVATION_STATUS_PENDING,
    RESERVATION_STATUS_REJECTED,
    RESERVATION_STATUS_TEXT,
    RESERVATION_STATUSES,
    STATUS_ABNORMAL,
    STATUS_NORMAL,
    STATUSES,
)

__all__ = [
    # 子模块
    "exceptions",
    "models",
    "table",
    # 业务异常
    "LaboratoryException",
    "LabNotFoundError",
    "LabClosedError",
    "LabNameDuplicateError",
    "EquipmentNotFoundError",
    "EquipmentCodeDuplicateError",
    "ReservationNotFoundError",
    "ReservationTimeInvalidError",
    "ReservationOutsideOpenTimeError",
    "ReservationCapacityError",
    "ReservationTimeConflictError",
    "ReservationStatusError",
    "ReservationPermissionError",
    "ReservationUserNotFoundError",
    # 状态常量
    "STATUS_ABNORMAL",
    "STATUS_NORMAL",
    "STATUSES",
    "RESERVATION_STATUS_PENDING",
    "RESERVATION_STATUS_APPROVED",
    "RESERVATION_STATUS_REJECTED",
    "RESERVATION_STATUS_CANCELLED",
    "RESERVATION_STATUSES",
    "RESERVATION_STATUS_TEXT",
    "OCCUPYING_STATUSES",
    # lab_services 里的全部业务函数与常量
    *_service_exports,
]
