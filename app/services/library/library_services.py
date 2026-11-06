"""图书馆自习室座位预约最上层逻辑：自习室 / 座位 / 预约

分层与命名完全对齐实验室模块（app/services/laboratory/lab_services.py）：
    实验室模块                      图书馆模块
    labs（实验室）           <->    library_study_rooms（自习室）
    lab_equipments（设备）   <->    library_seats（座位）
    lab_reservations（预约） <->    library_reservations（座位预约）

覆盖：
    自习室：新增 / 修改 / 删除 / 详情 / 条件列表 / 分页
    座位  ：批量新增 / 修改 / 删除 / 按自习室列表 / 分页 / 某时段空闲座位
    预约  ：提交 / 审核 / 取消 / 详情 / 分页 / 当天安排 / 某座位的空闲时段
    校验  ：座位占座冲突、同一人时间冲突、开放时间、预约日期与时长

和实验室预约的核心区别：
    实验室预约占的是「整间实验室」，同一时段只能有一条成功记录；
    自习室预约占的是「一个座位」，同一时段可以有很多人约同一间自习室的不同座位，
    所以冲突判定按 seat_id 算，容量天然由座位数保证（座位数不会超过自习室的 capacity）。

几个关键业务规则：
    1. 时段冲突用「半开区间」[start, end) 判断，公式是
       旧开始 < 新结束 AND 旧结束 > 新开始，
       所以 10:00-11:00 与 11:00-12:00 不算冲突，可以背靠背连排。
    2. 只有「预约成功」的记录才占座；待审核的申请不占座（否则谁都能靠刷申请把座位锁死），
      但待审核申请在管理员审核通过时会再查一次冲突，防止同一座位 / 同一人被批出两条。
    3. 自习室是「一人一座」：同一个人在同一时段只能有一个有效预约（跨座位也不行）。
    4. 预约必须同一天、不能早于当前时间、最多提前 MAX_ADVANCE_DAYS 天、
       单次时长不超过 MAX_RESERVE_HOURS 小时，并且必须落在自习室开放时间内（支持跨零点）。
    5. 默认「提交后待审核」（与实验室一致）；传 auto_approve=True 就是「先到先得、提交即成功」，
       此时提交前会加锁查冲突，抢座不会出现两个人同时占到同一个座位。
    6. 并发安全：提交预约 / 审核通过前都对座位行加锁（SELECT ... FOR UPDATE）。
    7. 申请人 / 处理人必须是真实存在的用户：user_id 上有指向 users 的真实外键（RESTRICT）做兜底，
       handler_id 是历史快照没有外键，两者都先由业务层查一次，换成明确的 404 提示。

事务约定：每个函数自己 commit；写入失败先回滚 Session（含冲突检测加的行锁）再抛
LibraryException 子类，所以上层捕获异常后可以安全地复用同一个 Session。
Session 的创建与关闭由调用方（接口层 / Agent 工具）负责。

鉴权边界：本文件不校验调用方是否真的是管理员（由上层鉴权负责，
见 app/services/user/auth_services.py 的 check_admin_or_raise），
只做「与操作人身份 / 归属相关」的校验（比如只能取消自己的预约）。
"""

from datetime import date, datetime, time, timedelta

from sqlalchemy import Select, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.schemas.models import PageResponse
from app.schemas.table import ROLE_ADMIN, ROLE_STUDENT, User
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
from app.services.library.models import (
    ReservationCancel,
    ReservationCreate,
    ReservationQuery,
    ReservationResponse,
    ReservationReview,
    RoomCreate,
    RoomQuery,
    RoomResponse,
    RoomUpdate,
    SeatBatchCreate,
    SeatQuery,
    SeatResponse,
    SeatUpdate,
    TimeSlot,
)
from app.services.library.table import (
    OCCUPYING_STATUSES,
    RESERVATION_STATUS_APPROVED,
    RESERVATION_STATUS_CANCELLED,
    RESERVATION_STATUS_PENDING,
    RESERVATION_STATUS_REJECTED,
    RESERVATION_STATUSES,
    STATUS_NORMAL,
    Seat,
    SeatReservation,
    StudyRoom,
)

__all__ = [
    # 常量
    "MAX_ADVANCE_DAYS",
    "MAX_RESERVE_HOURS",
    "SLOT_STEP_MINUTES",
    "ROLE_ADMIN",
    "ROLE_STUDENT",
    # 自习室
    "get_room_or_raise",
    "check_room_open_or_raise",
    "get_room_detail",
    "list_rooms",
    "get_room_page",
    "create_room",
    "update_room",
    "delete_room",
    # 座位
    "get_seat_or_raise",
    "check_seat_available_or_raise",
    "count_seats",
    "list_seats",
    "get_seat_page",
    "add_seats",
    "update_seat",
    "delete_seat",
    # 预约
    "check_reservation_or_raise",
    "get_reservation_detail",
    "get_reservation_page",
    "get_user_reservation_page",
    "get_handler_reservation_page",
    "get_all_reservation_page",
    "create_reservation",
    "review_reservation",
    "cancel_reservation",
    "get_room_schedule",
    "list_available_seats",
    "list_seat_free_slots",
    # 校验工具
    "find_seat_conflicts",
    "find_user_conflicts",
    "find_room_reservations",
    "is_seat_available",
    "is_user_free",
    "reserve_date_invalid_reason",
    "is_valid_reserve_date",
    "check_reserve_date_or_raise",
    "is_in_open_time",
]

# 最多可提前预约的天数（自习室座位通常只开放最近几天的预约）
MAX_ADVANCE_DAYS = 3
# 单次预约最长时长（小时）：防止长时间占座
MAX_RESERVE_HOURS = 4
# 查询座位空闲时段的默认粒度（分钟）
SLOT_STEP_MINUTES = 60

# 可更新字段白名单：避免主键、创建时间等被意外写入
ROOM_MUTABLE_FIELDS = (
    "name",
    "location",
    "capacity",
    "open_time",
    "close_time",
    "description",
    "img",
    "status",
)
# 座位不允许改所属自习室（room_id 不在白名单里），换座位请走「新增 + 删除」
SEAT_MUTABLE_FIELDS = ("code", "area", "has_power", "description", "status")

# 分页上限：防止上层传入超大 page_size 把库拖垮
MAX_PAGE_SIZE = 100


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


def _paginate(
    db: Session,
    query: Select,
    page: int,
    page_size: int,
    order_by,
) -> tuple[int, list]:
    """通用分页：先查总数，再按固定排序取当前页

    必须带 order_by，否则翻页时数据库返回顺序不稳定，会出现重复 / 漏数据。
    """
    total = int(db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows = db.scalars(
        query.order_by(order_by).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return total, list(rows)


def _normalize_datetime(value: datetime) -> datetime:
    """统一成本地 naive 时间，避免 aware / naive 比较抛 TypeError"""
    if value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value


def _validate_open_window(open_time: time | None, close_time: time | None) -> None:
    """校验开放时间窗：要么都为空（视为全天开放），要么都配置且不相同"""
    if (open_time is None) != (close_time is None):
        raise LibraryException("开放开始时间与结束时间必须同时配置", code=400)
    if open_time is not None and open_time == close_time:
        raise LibraryException("开放开始时间与结束时间不能相同", code=400)


# ==================================================================
# 一、自习室
# ==================================================================
def get_room_or_raise(db: Session, room_id: int) -> StudyRoom:
    """获取自习室，不存在则抛 RoomNotFoundError"""
    room = db.get(StudyRoom, room_id)
    if room is None:
        raise RoomNotFoundError(room_id)
    return room


def check_room_open_or_raise(db: Session, room_id: int) -> StudyRoom:
    """获取并检查自习室是否开放，不存在 -> 404，已关闭 -> 409"""
    room = get_room_or_raise(db, room_id)
    if room.status != STATUS_NORMAL:
        raise RoomClosedError(room_id)
    return room


def get_room_detail(db: Session, room_id: int) -> RoomResponse:
    """查询自习室详情"""
    return RoomResponse.model_validate(get_room_or_raise(db, room_id))


def _build_room_query(data: RoomQuery) -> Select:
    """根据条件构造自习室查询语句"""
    query = select(StudyRoom)

    if data.status is not None:
        query = query.where(StudyRoom.status == data.status)

    if data.room_ids:
        room_ids = [room_id for room_id in data.room_ids if room_id > 0]
        if room_ids:
            query = query.where(StudyRoom.id.in_(room_ids))

    if data.capacity is not None:
        # 需求座位数：查询容量不小于该值的自习室
        query = query.where(StudyRoom.capacity >= data.capacity)

    if data.keyword:
        like = f"%{_escape_like(data.keyword)}%"
        query = query.where(
            or_(
                StudyRoom.name.ilike(like, escape="\\"),
                StudyRoom.location.ilike(like, escape="\\"),
            )
        )

    return query


def list_rooms(
    db: Session,
    *,
    room_ids: list[int] | None = None,
    keyword: str | None = None,
    capacity: int | None = None,
    status: int | None = None,
) -> list[RoomResponse]:
    """条件查询自习室列表（不分页），用户页与管理页共用"""
    data = RoomQuery(
        room_ids=room_ids, keyword=keyword, capacity=capacity, status=status
    )
    rooms = db.scalars(_build_room_query(data).order_by(StudyRoom.id.desc())).all()
    return [RoomResponse.model_validate(room) for room in rooms]


def get_room_page(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 10,
    room_ids: list[int] | None = None,
    keyword: str | None = None,
    capacity: int | None = None,
    status: int | None = None,
) -> PageResponse[RoomResponse]:
    """分页查询自习室"""
    page, page_size = _normalize_page(page, page_size)
    data = RoomQuery(
        room_ids=room_ids, keyword=keyword, capacity=capacity, status=status
    )
    total, rooms = _paginate(
        db, _build_room_query(data), page, page_size, StudyRoom.id.desc()
    )
    return PageResponse[RoomResponse](
        total=total,
        page=page,
        page_size=page_size,
        data=[RoomResponse.model_validate(room) for room in rooms],
    )


def _check_room_name_unique_or_raise(
    db: Session, name: str, exclude_id: int | None = None
) -> None:
    """自习室名称查重

    exclude_id: 更新场景传入自身 id，避免把自己查出来误判为重复。
    数据库层面 name 上也有唯一索引兜底。
    """
    query = db.query(StudyRoom.id).filter(StudyRoom.name == name)
    if exclude_id is not None:
        query = query.filter(StudyRoom.id != exclude_id)
    if query.first() is not None:
        raise RoomNameDuplicateError(name)


def create_room(db: Session, data: RoomCreate) -> RoomResponse:
    """新增自习室"""
    _check_room_name_unique_or_raise(db, data.name)

    room = StudyRoom(
        name=data.name,
        location=data.location,
        capacity=data.capacity,
        open_time=data.open_time,
        close_time=data.close_time,
        description=data.description,
        img=data.img,
        status=data.status,
    )
    db.add(room)
    try:
        db.commit()
    except IntegrityError:
        # 唯一索引兜底：并发情况下先查重也会漏，交给数据库报错更安全
        db.rollback()
        raise RoomNameDuplicateError(data.name)
    db.refresh(room)
    return RoomResponse.model_validate(room)


def update_room(db: Session, data: RoomUpdate) -> RoomResponse:
    """更新自习室（增量更新，只写传入的字段）"""
    room = get_room_or_raise(db, data.id)

    # 名称变更时查重（排除自身）
    if data.name is not None and data.name != room.name:
        _check_room_name_unique_or_raise(db, data.name, exclude_id=room.id)

    # 容量不能被改到比已录入的座位数还小，否则座位会「超出容量」
    if data.capacity is not None:
        seat_count = count_seats(db, room_id=room.id)
        if data.capacity < seat_count:
            raise LibraryException(
                f"座位数上限不能小于已录入的座位数（当前 {seat_count} 个）", code=400
            )

    # 白名单 + exclude_unset：只更新显式传入的字段，避免主键 / 未传字段被覆盖
    room_dict = data.model_dump(exclude_unset=True)
    for field, value in room_dict.items():
        if field in ROOM_MUTABLE_FIELDS:
            setattr(room, field, value)

    # 开放时间可能与原值合并后才合法，因此在赋值后统一校验
    try:
        _validate_open_window(room.open_time, room.close_time)
    except LibraryException:
        db.rollback()  # 丢弃内存中已改动的字段，保证 Session 干净
        raise

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if data.name is not None:
            raise RoomNameDuplicateError(data.name)
        raise LibraryException("自习室数据冲突，更新失败", code=409)
    db.refresh(room)
    return RoomResponse.model_validate(room)


def delete_room(db: Session, room_id: int) -> None:
    """删除自习室

    存在预约记录时禁止删除（外键是 RESTRICT，直接删会抛 IntegrityError）；
    自习室下的座位由 cascade="all, delete-orphan" 级联删除。
    """
    room = get_room_or_raise(db, room_id)

    if (
        db.query(SeatReservation.id)
        .filter(SeatReservation.room_id == room_id)
        .first()
        is not None
    ):
        raise LibraryException("该自习室存在预约记录，无法删除", code=409)

    try:
        db.delete(room)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise LibraryException("该自习室存在关联数据，无法删除", code=409)


# ==================================================================
# 二、座位
# ==================================================================
def get_seat_or_raise(db: Session, seat_id: int) -> Seat:
    """获取座位，不存在则抛 SeatNotFoundError"""
    seat = db.get(Seat, seat_id)
    if seat is None:
        raise SeatNotFoundError(seat_id)
    return seat


def check_seat_available_or_raise(db: Session, seat_id: int) -> Seat:
    """获取并检查座位是否可预约

    座位不存在 -> 404；座位被停用 / 所属自习室已关闭 -> 409。
    """
    seat = get_seat_or_raise(db, seat_id)
    if seat.status != STATUS_NORMAL:
        raise SeatUnavailableError(f"座位 {seat.code} 当前不可用（已停用）")
    check_room_open_or_raise(db, seat.room_id)
    return seat


def count_seats(db: Session, *, room_id: int, only_available: bool = False) -> int:
    """统计自习室的座位数（only_available=True 时只算状态可用的座位）"""
    query = db.query(func.count(Seat.id)).filter(Seat.room_id == room_id)
    if only_available:
        query = query.filter(Seat.status == STATUS_NORMAL)
    return int(query.scalar() or 0)


def _check_seat_code_unique_or_raise(
    db: Session, code: str, exclude_id: int | None = None
) -> None:
    """单个座位编号查重"""
    query = db.query(Seat.id).filter(Seat.code == code)
    if exclude_id is not None:
        query = query.filter(Seat.id != exclude_id)
    if query.first() is not None:
        raise SeatCodeDuplicateError(code)


def _check_seat_codes_batch_or_raise(db: Session, codes: list[str]) -> None:
    """批量新增前统一查重：既查数据库，也查同一批次内部是否重复"""
    seen: set[str] = set()
    for code in codes:
        if code in seen:
            raise SeatCodeDuplicateError(code)
        seen.add(code)

    if not seen:
        return

    exists_row = db.query(Seat.code).filter(Seat.code.in_(seen)).first()
    if exists_row is not None:
        raise SeatCodeDuplicateError(exists_row[0])


def _build_seat_query(data: SeatQuery) -> Select:
    """根据条件构造座位查询语句"""
    query = select(Seat)

    if data.status is not None:
        query = query.where(Seat.status == data.status)

    if data.has_power is not None:
        query = query.where(Seat.has_power == data.has_power)

    if data.room_ids:
        room_ids = [room_id for room_id in data.room_ids if room_id > 0]
        if room_ids:
            query = query.where(Seat.room_id.in_(room_ids))

    if data.keyword:
        like = f"%{_escape_like(data.keyword)}%"
        query = query.where(
            or_(
                Seat.code.ilike(like, escape="\\"),
                Seat.area.ilike(like, escape="\\"),
                Seat.description.ilike(like, escape="\\"),
            )
        )

    return query


def list_seats(
    db: Session,
    room_id: int | None = None,
    *,
    keyword: str | None = None,
    status: int | None = None,
    has_power: bool | None = None,
) -> list[SeatResponse]:
    """查询座位列表（不分页）；传 room_id 则只看该自习室，自习室不存在时抛 404"""
    if room_id is not None:
        get_room_or_raise(db, room_id)

    data = SeatQuery(
        room_ids=[room_id] if room_id is not None else None,
        keyword=keyword,
        status=status,
        has_power=has_power,
    )
    seats = db.scalars(_build_seat_query(data).order_by(Seat.code.asc())).all()
    return [SeatResponse.model_validate(seat) for seat in seats]


def get_seat_page(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 10,
    room_ids: list[int] | None = None,
    keyword: str | None = None,
    status: int | None = None,
    has_power: bool | None = None,
) -> PageResponse[SeatResponse]:
    """分页查询座位"""
    page, page_size = _normalize_page(page, page_size)
    data = SeatQuery(
        room_ids=room_ids, keyword=keyword, status=status, has_power=has_power
    )
    total, seats = _paginate(
        db, _build_seat_query(data), page, page_size, Seat.id.desc()
    )
    return PageResponse[SeatResponse](
        total=total,
        page=page,
        page_size=page_size,
        data=[SeatResponse.model_validate(seat) for seat in seats],
    )


def add_seats(db: Session, data: SeatBatchCreate) -> list[SeatResponse]:
    """给指定自习室批量新增座位

    两条约束：
        * 座位编号全库唯一；
        * 录入后的座位数不能超过自习室的 capacity（容量即座位数上限），
          这样「按座位预约」天然不会超过自习室的容纳人数。
    座位状态默认「可用」；自习室可以处于关闭状态（管理员先建好座位再开放很常见）。
    """
    room = get_room_or_raise(db, data.room_id)
    _check_seat_codes_batch_or_raise(db, [seat.code for seat in data.seats])

    current = count_seats(db, room_id=room.id)
    if current + len(data.seats) > room.capacity:
        raise RoomCapacityExceededError(room.capacity, current, len(data.seats))

    seats = [
        Seat(
            code=seat.code,
            area=seat.area,
            has_power=seat.has_power,
            description=seat.description,
            room_id=data.room_id,
        )
        for seat in data.seats
    ]

    try:
        db.add_all(seats)
        db.commit()
    except IntegrityError:
        # 并发插入同一编号时唯一索引兜底
        db.rollback()
        raise SeatCodeDuplicateError()
    for seat in seats:
        db.refresh(seat)

    return [SeatResponse.model_validate(seat) for seat in seats]


def update_seat(db: Session, data: SeatUpdate) -> SeatResponse:
    """更新座位（增量更新）"""
    seat = get_seat_or_raise(db, data.id)

    if data.code is not None and data.code != seat.code:
        _check_seat_code_unique_or_raise(db, data.code, exclude_id=seat.id)

    seat_dict = data.model_dump(exclude_unset=True)
    for field, value in seat_dict.items():
        if field in SEAT_MUTABLE_FIELDS:
            setattr(seat, field, value)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if data.code is not None:
            raise SeatCodeDuplicateError(data.code)
        raise LibraryException("座位数据冲突，更新失败", code=409)
    db.refresh(seat)
    return SeatResponse.model_validate(seat)


def delete_seat(db: Session, seat_id: int) -> None:
    """删除座位

    座位存在预约记录时禁止删除（外键是 RESTRICT），否则历史预约会变成孤儿数据。
    """
    seat = get_seat_or_raise(db, seat_id)

    if (
        db.query(SeatReservation.id)
        .filter(SeatReservation.seat_id == seat_id)
        .first()
        is not None
    ):
        raise LibraryException("该座位存在预约记录，无法删除", code=409)

    try:
        db.delete(seat)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise LibraryException("该座位存在关联数据，无法删除", code=409)


# ==================================================================
# 三、预约校验工具
# ==================================================================
def find_seat_conflicts(
    db: Session,
    seat_id: int,
    start_time: datetime,
    end_time: datetime,
    *,
    exclude_id: int | None = None,
    statuses: tuple[int, ...] = OCCUPYING_STATUSES,
) -> list[SeatReservation]:
    """查询该座位在 [start_time, end_time) 已被占用的预约记录

    重叠判定公式（两区间相交的标准写法）：
        新开始 < 旧结束  AND  旧开始 < 新结束

    为什么用严格小于 / 严格大于，而不是 <= / >= ？
        因为区间是半开的 [start, end)：
            旧: 10:00 - 11:00   新: 11:00 - 12:00
            新开始(11:00) < 旧结束(11:00) -> False -> 不算冲突（可以背靠背连排）

    exclude_id: 审核场景排除自身，否则「自己和自己重叠」会导致永远审核不通过。
    返回完整对象（而不是 bool），是为了让上层能提示「被哪个单号占了」。
    """
    start_time = _normalize_datetime(start_time)
    end_time = _normalize_datetime(end_time)

    query = db.query(SeatReservation).filter(
        SeatReservation.seat_id == seat_id,
        SeatReservation.status.in_(statuses),
        SeatReservation.reserve_start_time < end_time,  # 旧开始 < 新结束
        SeatReservation.reserve_end_time > start_time,  # 旧结束 > 新开始
    )
    if exclude_id is not None:
        query = query.filter(SeatReservation.id != exclude_id)

    return query.order_by(SeatReservation.reserve_start_time).all()


def find_user_conflicts(
    db: Session,
    user_id: int,
    start_time: datetime,
    end_time: datetime,
    *,
    exclude_id: int | None = None,
    statuses: tuple[int, ...] = OCCUPYING_STATUSES,
) -> list[SeatReservation]:
    """查询该用户在 [start_time, end_time) 已有的有效预约（跨座位）

    自习室是「一人一座」：同一个人同一时段不能同时占两个座位，
    所以除了座位维度，还要按 user_id 再查一次。
    """
    start_time = _normalize_datetime(start_time)
    end_time = _normalize_datetime(end_time)

    query = db.query(SeatReservation).filter(
        SeatReservation.user_id == user_id,
        SeatReservation.status.in_(statuses),
        SeatReservation.reserve_start_time < end_time,
        SeatReservation.reserve_end_time > start_time,
    )
    if exclude_id is not None:
        query = query.filter(SeatReservation.id != exclude_id)

    return query.order_by(SeatReservation.reserve_start_time).all()


def find_room_reservations(
    db: Session,
    room_id: int,
    start_time: datetime,
    end_time: datetime,
    *,
    statuses: tuple[int, ...] = OCCUPYING_STATUSES,
) -> list[SeatReservation]:
    """查询某自习室在 [start_time, end_time) 的预约记录（不分座位，用于看当天安排）"""
    start_time = _normalize_datetime(start_time)
    end_time = _normalize_datetime(end_time)

    return (
        db.query(SeatReservation)
        .filter(
            SeatReservation.room_id == room_id,
            SeatReservation.status.in_(statuses),
            SeatReservation.reserve_start_time < end_time,
            SeatReservation.reserve_end_time > start_time,
        )
        .order_by(SeatReservation.reserve_start_time, SeatReservation.seat_code)
        .all()
    )


def is_seat_available(
    db: Session,
    seat_id: int,
    start_time: datetime,
    end_time: datetime,
    *,
    exclude_id: int | None = None,
) -> bool:
    """座位在该时段是否可预约（只需要布尔值时用这个）"""
    return not find_seat_conflicts(
        db, seat_id, start_time, end_time, exclude_id=exclude_id
    )


def is_user_free(
    db: Session,
    user_id: int,
    start_time: datetime,
    end_time: datetime,
    *,
    exclude_id: int | None = None,
) -> bool:
    """该用户在该时段是否还没有别的有效预约"""
    return not find_user_conflicts(
        db, user_id, start_time, end_time, exclude_id=exclude_id
    )


def is_in_open_time(room: StudyRoom, start: time, end: time) -> bool:
    """判断时间段是否落在自习室开放时间内（只比时钟，不看日期）

    未配置开放时间 -> 视为全天开放；
    open_time > close_time 表示跨零点开放（如 22:00 - 次日 02:00），
    此时开放区间是 [open, 24:00) ∪ [00:00, close)，
    只要 [start, end) 完整落在其中一段即可。
    """
    if room.open_time is None or room.close_time is None:
        return True

    open_time = room.open_time
    close_time = room.close_time

    if open_time <= close_time:
        return open_time <= start and end <= close_time

    # 跨零点：[open, 24:00) ∪ [00:00, close)
    return start >= open_time or end <= close_time


def reserve_date_invalid_reason(
    start: datetime, end: datetime, *, now: datetime | None = None
) -> str:
    """预约时间合法性判断，返回不合法原因；返回空字符串表示合法

    规则：必须是同一天、结束时间晚于开始时间、开始时间不能早于当前时间、
    最多提前 MAX_ADVANCE_DAYS 天、单次时长不超过 MAX_RESERVE_HOURS 小时。
    返回原因字符串是为了让上层能给用户明确提示。
    """
    now = _normalize_datetime(now) if now is not None else datetime.now()
    start = _normalize_datetime(start)
    end = _normalize_datetime(end)

    if start.date() != end.date():
        return "预约开始与结束时间必须在同一天"
    if end <= start:
        return "预约结束时间必须晚于开始时间"
    if start < now:
        return "不能预约已经过去的时间"
    if start > now + timedelta(days=MAX_ADVANCE_DAYS):
        return f"最多只能提前 {MAX_ADVANCE_DAYS} 天预约"
    if end - start > timedelta(hours=MAX_RESERVE_HOURS):
        return f"单次预约时长不能超过 {MAX_RESERVE_HOURS} 小时"
    return ""


def is_valid_reserve_date(
    start: datetime, end: datetime, *, now: datetime | None = None
) -> bool:
    """预约时间是否合法（只需要布尔值时用这个）"""
    return reserve_date_invalid_reason(start, end, now=now) == ""


def check_reserve_date_or_raise(
    start: datetime, end: datetime, *, now: datetime | None = None
) -> None:
    """预约时间不合法时抛 ReservationTimeInvalidError"""
    reason = reserve_date_invalid_reason(start, end, now=now)
    if reason:
        raise ReservationTimeInvalidError(reason)


# ==================================================================
# 四、座位预约业务
# ==================================================================
def check_reservation_or_raise(db: Session, reservation_id: int) -> SeatReservation:
    """获取预约记录，不存在则抛 ReservationNotFoundError"""
    reservation = db.get(SeatReservation, reservation_id)
    if reservation is None:
        raise ReservationNotFoundError(reservation_id)
    return reservation


def _check_user_exists_or_raise(db: Session, user_id: int, *, label: str = "用户") -> None:
    """校验预约要关联的用户真实存在，不存在则抛 ReservationUserNotFoundError(404)

    为什么业务层还要查一次（数据库已经有外键了）？
        * 外键抛的是 IntegrityError，上层只能看到一句「数据库报错」，这里能给出「申请用户不存在: 7」；
        * handler_id 是故意不加外键的历史快照，只有业务层这一道校验。
    用户表是全项目通用的（app/schemas/table.py），在这里 import 不算业务模块之间的强耦合。
    """
    if db.get(User, user_id) is None:
        raise ReservationUserNotFoundError(user_id, label=label)


def get_reservation_detail(db: Session, reservation_id: int) -> ReservationResponse:
    """查询预约详情"""
    return ReservationResponse.model_validate(
        check_reservation_or_raise(db, reservation_id)
    )


def create_reservation(
    db: Session,
    data: ReservationCreate,
    *,
    user_id: int,
    user_name: str,
    auto_approve: bool = False,
) -> ReservationResponse:
    """提交座位预约

    申请人身份由上层传入（登录态 / Agent 调用上下文），本模块不接收前端传来的姓名，
    避免伪造姓名或替别人占座。

    auto_approve:
        False（默认，与实验室预约一致）-> 生成「待审核」记录，等管理员审核；
        True（先到先得）              -> 加锁查冲突后直接落成「预约成功」。
    无论哪种模式，待审核记录都不占座，只有「预约成功」才占座。
    """
    # 1. 申请人必须真实存在（user_id 上有外键，这里提前查一次给出友好提示）
    _check_user_exists_or_raise(db, user_id, label="申请用户")

    # 2. 座位必须可用（座位本身没停用，且所属自习室开放）
    seat = check_seat_available_or_raise(db, data.seat_id)
    room = get_room_or_raise(db, seat.room_id)

    # 3. 时间合法性：同一天、不在过去、不超过可提前天数、不超过单次时长上限
    check_reserve_date_or_raise(data.reserve_start_time, data.reserve_end_time)

    # 4. 必须落在开放时间内
    if not is_in_open_time(
        room, data.reserve_start_time.time(), data.reserve_end_time.time()
    ):
        open_text = (
            f"{room.open_time:%H:%M}-{room.close_time:%H:%M}"
            if room.open_time and room.close_time
            else "未配置"
        )
        raise ReservationOutsideOpenTimeError(
            f"预约时间不在开放时间内（开放时间: {open_text}）"
        )

    # 5. 对座位行加锁，串行化同一座位的并发抢座，避免 check-then-insert 竞态
    db.query(Seat).filter(Seat.id == seat.id).with_for_update().first()

    # 6. 座位冲突（只有「预约成功」的记录占座）
    conflicts = find_seat_conflicts(
        db, seat.id, data.reserve_start_time, data.reserve_end_time
    )
    if conflicts:
        # 明细在异常对象里已快照，回滚只会释放行锁，不影响上层读取冲突详情
        error = ReservationTimeConflictError(conflicts)
        db.rollback()
        raise error

    # 7. 同一个人同一时段只能有一个有效预约
    user_conflicts = find_user_conflicts(
        db, user_id, data.reserve_start_time, data.reserve_end_time
    )
    if user_conflicts:
        error = ReservationUserConflictError(
            "您在该时段已有座位预约"
            f"（{user_conflicts[0].reservation_no}，座位 {user_conflicts[0].seat_code}），"
            "请先取消后再预约"
        )
        db.rollback()
        raise error

    # 8. 落库
    reservation = SeatReservation(
        room_id=room.id,
        room_name=room.name,
        seat_id=seat.id,
        seat_code=seat.code,
        user_id=user_id,
        user_name=user_name,
        reserve_start_time=data.reserve_start_time,
        reserve_end_time=data.reserve_end_time,
        status=(
            RESERVATION_STATUS_APPROVED if auto_approve else RESERVATION_STATUS_PENDING
        ),
    )
    db.add(reservation)
    try:
        db.commit()
    except IntegrityError:
        # 预约单号唯一索引 / user_id 外键兜底（并发场景下先查也会漏，交给数据库报错更安全）
        db.rollback()
        if db.get(User, user_id) is None:
            raise ReservationUserNotFoundError(user_id, label="申请用户")
        raise LibraryException("预约单号生成冲突，请重试", code=409)
    db.refresh(reservation)
    return ReservationResponse.model_validate(reservation)


def review_reservation(
    db: Session,
    data: ReservationReview,
    *,
    handler_id: int,
    handler_name: str,
) -> ReservationResponse:
    """管理员审核预约：1-通过 / 2-驳回

    * 已通过 / 已取消的预约不允许再审核（避免把已有结论的记录改来改去）
    * 审核通过前复检两类冲突（排除自身）：
        座位维度——避免同一个座位被批出两条重叠的成功记录；
        申请人维度——避免同一个人在重叠时段被批出两条成功记录。
    * handler_id 只是历史快照（没有外键），但仍然要求处理人真实存在
    """
    reservation = check_reservation_or_raise(db, data.id)

    if reservation.status == RESERVATION_STATUS_CANCELLED:
        raise ReservationStatusError("预约已取消，无法审核")
    if reservation.status == RESERVATION_STATUS_APPROVED:
        raise ReservationStatusError("预约已通过审核，请勿重复操作")

    _check_user_exists_or_raise(db, handler_id, label="处理人(管理员)")

    if data.status == RESERVATION_STATUS_APPROVED:
        # 加锁 + 复检冲突：并发审核同一座位时，只会有第一条成功
        db.query(Seat).filter(Seat.id == reservation.seat_id).with_for_update().first()

        conflicts = find_seat_conflicts(
            db,
            reservation.seat_id,
            reservation.reserve_start_time,
            reservation.reserve_end_time,
            exclude_id=reservation.id,
        )
        if conflicts:
            error = ReservationTimeConflictError(conflicts)
            db.rollback()  # 释放座位行锁
            raise error

        user_conflicts = find_user_conflicts(
            db,
            reservation.user_id,
            reservation.reserve_start_time,
            reservation.reserve_end_time,
            exclude_id=reservation.id,
        )
        if user_conflicts:
            error = ReservationUserConflictError(
                f"该申请人在此时段已有其它预约（{user_conflicts[0].reservation_no}），"
                "请先取消后再通过"
            )
            db.rollback()
            raise error

    reservation.status = data.status
    if data.remark is not None:
        reservation.remark = data.remark
    reservation.handler_id = handler_id
    reservation.handler_name = handler_name
    reservation.handle_time = datetime.now()

    db.commit()
    db.refresh(reservation)
    return ReservationResponse.model_validate(reservation)


def cancel_reservation(
    db: Session,
    data: ReservationCancel,
    *,
    user_id: int,
    user_role: str = ROLE_STUDENT,
) -> ReservationResponse:
    """取消预约

    * 申请方只能取消自己的预约，管理员可以代取消
    * 待审核、已成功的预约都可以取消（取消后自动释放座位）
    * 已取消、已驳回属于终态，不需要也不能重复取消
    """
    reservation = check_reservation_or_raise(db, data.id)

    if user_role != ROLE_ADMIN and reservation.user_id != user_id:
        raise ReservationPermissionError("只能取消自己的预约")

    if reservation.status == RESERVATION_STATUS_CANCELLED:
        raise ReservationStatusError("预约已取消，无需重复操作")
    if reservation.status == RESERVATION_STATUS_REJECTED:
        raise ReservationStatusError("预约未通过审核，无需取消")

    reservation.status = RESERVATION_STATUS_CANCELLED
    if data.remark is not None:
        reservation.remark = data.remark

    db.commit()
    db.refresh(reservation)
    return ReservationResponse.model_validate(reservation)


def _normalize_statuses(status: int | list[int] | None) -> list[int] | None:
    """把「单个状态」或「状态列表」统一成去重后的列表，并校验取值"""
    if status is None:
        return None

    values = [status] if isinstance(status, int) else list(status)
    normalized = sorted({int(item) for item in values})
    if not normalized:
        return None

    invalid = [item for item in normalized if item not in RESERVATION_STATUSES]
    if invalid:
        raise ReservationStatusError(f"预约状态取值不合法: {invalid}", code=400)
    return normalized


def _build_reservation_query(data: ReservationQuery) -> Select:
    """根据条件构造预约查询语句"""
    query = select(SeatReservation)

    if data.user_id is not None:
        query = query.where(SeatReservation.user_id == data.user_id)
    if data.handler_id is not None:
        query = query.where(SeatReservation.handler_id == data.handler_id)

    if data.room_ids:
        room_ids = [room_id for room_id in data.room_ids if room_id > 0]
        if room_ids:
            query = query.where(SeatReservation.room_id.in_(room_ids))

    if data.seat_ids:
        seat_ids = [seat_id for seat_id in data.seat_ids if seat_id > 0]
        if seat_ids:
            query = query.where(SeatReservation.seat_id.in_(seat_ids))

    statuses = _normalize_statuses(data.status)
    if statuses is not None:
        query = query.where(SeatReservation.status.in_(statuses))

    if data.keyword:
        like = f"%{_escape_like(data.keyword)}%"
        query = query.where(
            or_(
                SeatReservation.reservation_no.ilike(like, escape="\\"),
                SeatReservation.room_name.ilike(like, escape="\\"),
                SeatReservation.seat_code.ilike(like, escape="\\"),
                SeatReservation.user_name.ilike(like, escape="\\"),
            )
        )

    # 时间段筛选：查与 [start_time, end_time) 有交集的预约（与冲突判定同一套区间逻辑）
    if data.start_time is not None:
        query = query.where(
            SeatReservation.reserve_end_time > _normalize_datetime(data.start_time)
        )
    if data.end_time is not None:
        query = query.where(
            SeatReservation.reserve_start_time < _normalize_datetime(data.end_time)
        )

    return query


def get_reservation_page(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 10,
    status: int | list[int] | None = None,
    room_ids: list[int] | None = None,
    seat_ids: list[int] | None = None,
    user_id: int | None = None,
    handler_id: int | None = None,
    keyword: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> PageResponse[ReservationResponse]:
    """分页查询预约记录（管理员查全部、学生查自己的都走这里）"""
    page, page_size = _normalize_page(page, page_size)
    data = ReservationQuery(
        status=status,
        room_ids=room_ids,
        seat_ids=seat_ids,
        user_id=user_id,
        handler_id=handler_id,
        keyword=keyword,
        start_time=start_time,
        end_time=end_time,
    )
    total, rows = _paginate(
        db, _build_reservation_query(data), page, page_size, SeatReservation.id.desc()
    )
    return PageResponse[ReservationResponse](
        total=total,
        page=page,
        page_size=page_size,
        data=[ReservationResponse.model_validate(row) for row in rows],
    )


def get_user_reservation_page(
    db: Session,
    user_id: int,
    *,
    page: int = 1,
    page_size: int = 10,
    status: int | list[int] | None = None,
    keyword: str | None = None,
) -> PageResponse[ReservationResponse]:
    """当前用户的预约列表（My 预约）"""
    return get_reservation_page(
        db,
        page=page,
        page_size=page_size,
        status=status,
        user_id=user_id,
        keyword=keyword,
    )


def get_handler_reservation_page(
    db: Session,
    handler_id: int,
    *,
    page: int = 1,
    page_size: int = 10,
    status: int | list[int] | None = None,
    keyword: str | None = None,
) -> PageResponse[ReservationResponse]:
    """当前管理员处理过的预约列表"""
    return get_reservation_page(
        db,
        page=page,
        page_size=page_size,
        status=status,
        handler_id=handler_id,
        keyword=keyword,
    )


def get_all_reservation_page(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 10,
    status: int | list[int] | None = None,
    room_ids: list[int] | None = None,
    seat_ids: list[int] | None = None,
    keyword: str | None = None,
) -> PageResponse[ReservationResponse]:
    """管理员查看全部预约

    待审核（status=0）的预约 handler_id 为空，不属于任何管理员的「已处理」记录，
    审核待办需要能看到这些记录，因此单独提供不按 handler_id 过滤的分页查询。
    """
    return get_reservation_page(
        db,
        page=page,
        page_size=page_size,
        status=status,
        room_ids=room_ids,
        seat_ids=seat_ids,
        keyword=keyword,
    )


def _day_window(room: StudyRoom, day: date) -> tuple[datetime, datetime]:
    """自习室在某天的可预约时间窗（同一天内，最晚到 23:59:59.999999）

    跨零点开放（open_time > close_time）时只取当天的 [open, 24:00)：
    预约必须同一天，后半夜那段属于次日的时间窗。
    """
    day_start = datetime.combine(day, time.min)
    day_end = datetime.combine(day, time.max)

    if room.open_time is None or room.close_time is None:
        return day_start, day_end

    open_dt = datetime.combine(day, room.open_time)
    close_dt = datetime.combine(day, room.close_time)
    if close_dt <= open_dt:
        close_dt = day_end
    return open_dt, close_dt


def get_room_schedule(
    db: Session,
    room_id: int,
    day: date | None = None,
    *,
    include_pending: bool = True,
) -> list[ReservationResponse]:
    """查询某自习室某天的预约安排（按开始时间和座位号排序）

    include_pending=True（默认）时连待审核的申请一起返回，方便管理员看排队情况；
    False 时只看真正占座的「预约成功」记录。
    """
    get_room_or_raise(db, room_id)
    day = day or datetime.now().date()
    window_start = datetime.combine(day, time.min)
    window_end = window_start + timedelta(days=1)

    statuses = (
        (RESERVATION_STATUS_PENDING, RESERVATION_STATUS_APPROVED)
        if include_pending
        else OCCUPYING_STATUSES
    )
    rows = find_room_reservations(
        db, room_id, window_start, window_end, statuses=statuses
    )
    return [ReservationResponse.model_validate(row) for row in rows]


def list_available_seats(
    db: Session,
    room_id: int,
    start_time: datetime,
    end_time: datetime,
) -> list[SeatResponse]:
    """列出某自习室在指定时段内还空着的座位

    这是自习室场景最常用的查询（「这段时间还有哪些座位」）：
    只返回状态可用、且在该时段没有「预约成功」记录的座位。
    用 NOT EXISTS 子查询在数据库里直接过滤，不把整间自习室的预约都拉到内存再算。

    自习室不存在 -> 404；自习室已关闭 -> 409（关闭的自习室没有可约座位，
    给出明确提示比返回空列表更容易定位问题）。
    """
    start_time = _normalize_datetime(start_time)
    end_time = _normalize_datetime(end_time)
    if end_time <= start_time:
        raise LibraryException("结束时间必须晚于开始时间", code=400)

    check_room_open_or_raise(db, room_id)

    busy = exists().where(
        SeatReservation.seat_id == Seat.id,
        SeatReservation.status.in_(OCCUPYING_STATUSES),
        SeatReservation.reserve_start_time < end_time,
        SeatReservation.reserve_end_time > start_time,
    )

    query = (
        select(Seat)
        .where(Seat.room_id == room_id, Seat.status == STATUS_NORMAL, ~busy)
        .order_by(Seat.code.asc())
    )
    seats = db.scalars(query).all()
    return [SeatResponse.model_validate(seat) for seat in seats]


def list_seat_free_slots(
    db: Session,
    seat_id: int,
    day: date | None = None,
    *,
    step_minutes: int = SLOT_STEP_MINUTES,
    now: datetime | None = None,
) -> list[TimeSlot]:
    """列出某个座位某天的可预约时段

    以 step_minutes 为粒度切分开放时间窗，标记每个时段是否被「预约成功」的记录占用；
    已经过去的时间直接从窗口里裁掉，所以第一个时段可能是半截的。
    """
    if step_minutes <= 0 or step_minutes > 24 * 60:
        raise LibraryException("时段粒度必须在 1 ~ 1440 分钟之间", code=400)

    seat = check_seat_available_or_raise(db, seat_id)
    room = get_room_or_raise(db, seat.room_id)
    now = _normalize_datetime(now) if now is not None else datetime.now()
    day = day or now.date()

    open_dt, close_dt = _day_window(room, day)
    window_start = max(open_dt, now)
    window_end = close_dt
    if window_start >= window_end:
        return []

    busy = [
        (item.reserve_start_time, item.reserve_end_time)
        for item in find_seat_conflicts(db, seat.id, window_start, window_end)
    ]

    step = timedelta(minutes=step_minutes)
    slots: list[TimeSlot] = []
    cursor = window_start
    while cursor < window_end:
        slot_end = min(cursor + step, window_end)
        available = all(
            not (cursor < busy_end and busy_start < slot_end)
            for busy_start, busy_end in busy
        )
        slots.append(TimeSlot(start_time=cursor, end_time=slot_end, available=available))
        cursor = slot_end
    return slots
