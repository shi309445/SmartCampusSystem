"""图书馆业务模块（app/services/library）

目录结构（与 app/services/laboratory 完全对齐）：
    table.py            图书馆业务专用表: library_study_rooms / library_seats / library_reservations
    models.py           图书馆业务数据模型（请求 / 响应 / 查询）
    exceptions.py       图书馆业务异常（按 code 映射 HTTP 状态码）
    library_services.py 最上层业务逻辑：自习室增删改查、座位增删改查、座位预约与审核

全项目通用的表和数据模型在 app/schemas/table.py 与 app/schemas/models.py；
用户（身份 / 鉴权）在 app/services/user。

与实验室预约的对应关系：
    实验室 labs / lab_equipments / lab_reservations
    图书馆 study_rooms（自习室） / seats（座位） / seat_reservations（座位预约）
实验室预约占「整间实验室」，自习室预约占「一个座位」，因此冲突按 seat_id 判定，
同一时段允许多人预约同一间自习室的不同座位。

用法示例：
    from app.database import SessionLocal
    from app.services.library import library_services as lib
    from app.services.library.models import SeatBatchCreate, SeatCreate, ReservationCreate

    with SessionLocal() as db:
        room = lib.create_room(db, RoomCreate(name="三楼自习室", capacity=50,
                                              open_time=time(8), close_time=time(22)))
        lib.add_seats(db, SeatBatchCreate(room_id=room.id, seats=[
            SeatCreate(code="3F-A01", area="靠窗", has_power=True),
        ]))
        lib.create_reservation(db, ReservationCreate(seat_id=1, ...),
                               user_id=1, user_name="张三", auto_approve=True)
"""

from app.services.library import exceptions, library_services, models, table
from app.services.library.exceptions import (
    LibraryException,
    ReservationNotFoundError,
    ReservationOutsideOpenTimeError,
    ReservationPermissionError,
    ReservationStatusError,
    ReservationTimeConflictError,
    ReservationTimeInvalidError,
    ReservationUserConflictError,
    ReservationUserNotFoundError,
    RoomCapacityExceededError,
    RoomClosedError,
    RoomNameDuplicateError,
    RoomNotFoundError,
    SeatCodeDuplicateError,
    SeatNotFoundError,
    SeatUnavailableError,
)
from app.services.library.library_services import *  # noqa: F403
from app.services.library.library_services import __all__ as _service_exports
from app.services.library.table import (
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
    "library_services",
    "models",
    "table",
    # 业务异常
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
    # library_services 里的全部业务函数与常量
    *_service_exports,
]
