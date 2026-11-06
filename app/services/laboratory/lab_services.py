"""实验室业务最上层逻辑：实验室 / 设备 / 预约

本文件是实验室模块唯一的对外业务入口，覆盖：
    实验室：新增 / 修改 / 删除 / 详情 / 条件列表 / 分页
    设备  ：批量新增 / 修改 / 删除 / 按实验室列表 / 分页
    预约  ：提交申请 / 审核 / 取消 / 详情 / 分页 / 当天安排 / 可预约时段
    校验  ：时段冲突检测、开放时间、预约日期、人数容量

几个关键业务规则（与参考实现保持一致）：
    1. 时段冲突用「半开区间」[start, end) 判断，公式是
       旧开始 < 新结束 AND 旧结束 > 新开始，
       所以 10:00-11:00 与 11:00-12:00 不算冲突，可以背靠背连排。
    2. 只有「审核通过」的预约才占用时段；待审核的申请不占用时段，
       避免任何人靠狂刷申请占满实验室。
    3. 预约必须同一天、不能早于当前时间、最多提前 MAX_ADVANCE_DAYS 天，
       并且必须落在实验室的开放时间内（支持跨零点开放）。
    4. 并发安全：提交预约 / 审核通过前都对实验室行加锁（SELECT ... FOR UPDATE），
       把同一实验室的 check-then-insert 串行化，避免同一时段被批成两条成功记录。
    5. 申请人 / 处理人必须是真实存在的用户：user_id 上有指向 users 的真实外键（RESTRICT）做兜底，
       handler_id 是历史快照没有外键，两者都先由业务层查一次，换成明确的 404 提示。

事务约定：每个函数自己 commit；写入失败时先把 Session 回滚（含冲突检测加的行锁）
再抛 LaboratoryException 子类，所以上层捕获异常后可以安全地复用同一个 Session。
Session 的创建与关闭由调用方（接口层 / Agent 工具）负责。
"""

from datetime import date, datetime, time, timedelta

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.schemas.models import PageResponse
from app.schemas.table import ROLE_ADMIN, ROLE_STUDENT, User
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
from app.services.laboratory.models import (
    EquipmentBatchCreate,
    EquipmentQuery,
    EquipmentResponse,
    EquipmentUpdate,
    LabCreate,
    LabQuery,
    LabResponse,
    LabUpdate,
    ReservationCancel,
    ReservationCreate,
    ReservationQuery,
    ReservationResponse,
    ReservationReview,
    TimeSlot,
)
from app.services.laboratory.table import (
    OCCUPYING_STATUSES,
    RESERVATION_STATUS_APPROVED,
    RESERVATION_STATUS_CANCELLED,
    RESERVATION_STATUS_PENDING,
    RESERVATION_STATUS_REJECTED,
    RESERVATION_STATUSES,
    STATUS_NORMAL,
    Equipment,
    Lab,
    LabReservation,
)

__all__ = [
    # 常量
    "MAX_ADVANCE_DAYS",
    "SLOT_STEP_MINUTES",
    "ROLE_ADMIN",
    "ROLE_STUDENT",
    # 实验室
    "get_lab_or_raise",
    "check_lab_open_or_raise",
    "get_lab_detail",
    "list_labs",
    "get_lab_page",
    "create_lab",
    "update_lab",
    "delete_lab",
    # 设备
    "get_equipment_or_raise",
    "list_equipments",
    "get_equipment_page",
    "add_equipments",
    "update_equipment",
    "delete_equipment",
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
    "get_lab_schedule",
    "list_available_slots",
    # 校验工具
    "find_conflicting_reservations",
    "is_time_available",
    "reserve_date_invalid_reason",
    "is_valid_reserve_date",
    "check_reserve_date_or_raise",
    "is_in_open_time",
]

# 最多可提前预约的天数
MAX_ADVANCE_DAYS = 7
# 查询可预约时段的默认粒度（分钟）
SLOT_STEP_MINUTES = 60
# 角色标识统一取自全项目通用的 app/schemas/table.py，避免各处写死字符串
# （ROLE_ADMIN / ROLE_STUDENT 已在本模块顶部导入，这里沿用同名对外导出）

# 可更新字段白名单：避免主键、创建时间等被意外写入
LAB_MUTABLE_FIELDS = (
    "name",
    "location",
    "maxpeople",
    "open_time",
    "close_time",
    "description",
    "img",
    "status",
)
EQUIPMENT_MUTABLE_FIELDS = ("name", "code", "model", "description", "status")

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
        raise LaboratoryException("开放开始时间与结束时间必须同时配置", code=400)
    if open_time is not None and open_time == close_time:
        raise LaboratoryException("开放开始时间与结束时间不能相同", code=400)


# ==================================================================
# 一、实验室
# ==================================================================
def get_lab_or_raise(db: Session, lab_id: int) -> Lab:
    """获取实验室，不存在则抛 LabNotFoundError"""
    lab = db.get(Lab, lab_id)
    if lab is None:
        raise LabNotFoundError(lab_id)
    return lab


def check_lab_open_or_raise(db: Session, lab_id: int) -> Lab:
    """获取并检查实验室是否开放，不存在 -> 404，已关闭 -> 409"""
    lab = get_lab_or_raise(db, lab_id)
    if lab.status != STATUS_NORMAL:
        raise LabClosedError(lab_id)
    return lab


def get_lab_detail(db: Session, lab_id: int) -> LabResponse:
    """查询实验室详情"""
    return LabResponse.model_validate(get_lab_or_raise(db, lab_id))


def _build_lab_query(data: LabQuery) -> Select:
    """根据条件构造实验室查询语句"""
    query = select(Lab)

    if data.status is not None:
        query = query.where(Lab.status == data.status)

    if data.lab_ids:
        lab_ids = [lab_id for lab_id in data.lab_ids if lab_id > 0]
        if lab_ids:
            query = query.where(Lab.id.in_(lab_ids))

    if data.people_num is not None:
        # 需求人数：查询能容纳该人数的实验室
        query = query.where(Lab.maxpeople >= data.people_num)

    if data.keyword:
        like = f"%{_escape_like(data.keyword)}%"
        query = query.where(
            or_(
                Lab.name.ilike(like, escape="\\"),
                Lab.location.ilike(like, escape="\\"),
            )
        )

    return query


def list_labs(
    db: Session,
    *,
    lab_ids: list[int] | None = None,
    keyword: str | None = None,
    people_num: int | None = None,
    status: int | None = None,
) -> list[LabResponse]:
    """条件查询实验室列表（不分页），用户页与管理页共用"""
    data = LabQuery(
        lab_ids=lab_ids, keyword=keyword, people_num=people_num, status=status
    )
    labs = db.scalars(_build_lab_query(data).order_by(Lab.id.desc())).all()
    return [LabResponse.model_validate(lab) for lab in labs]


def get_lab_page(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 10,
    lab_ids: list[int] | None = None,
    keyword: str | None = None,
    people_num: int | None = None,
    status: int | None = None,
) -> PageResponse[LabResponse]:
    """分页查询实验室"""
    page, page_size = _normalize_page(page, page_size)
    data = LabQuery(
        lab_ids=lab_ids, keyword=keyword, people_num=people_num, status=status
    )
    total, labs = _paginate(
        db, _build_lab_query(data), page, page_size, Lab.id.desc()
    )
    return PageResponse[LabResponse](
        total=total,
        page=page,
        page_size=page_size,
        data=[LabResponse.model_validate(lab) for lab in labs],
    )


def _check_lab_name_unique_or_raise(
    db: Session, name: str, exclude_id: int | None = None
) -> None:
    """实验室名称查重

    exclude_id: 更新场景传入自身 id，避免把自己查出来误判为重复。
    数据库层面 name 上也有唯一索引兜底。
    """
    query = db.query(Lab.id).filter(Lab.name == name)
    if exclude_id is not None:
        query = query.filter(Lab.id != exclude_id)
    if query.first() is not None:
        raise LabNameDuplicateError(name)


def create_lab(db: Session, data: LabCreate) -> LabResponse:
    """新增实验室"""
    _check_lab_name_unique_or_raise(db, data.name)

    lab = Lab(
        name=data.name,
        location=data.location,
        maxpeople=data.maxpeople,
        open_time=data.open_time,
        close_time=data.close_time,
        description=data.description,
        img=data.img,
        status=data.status,
    )
    db.add(lab)
    try:
        db.commit()
    except IntegrityError:
        # 唯一索引兜底：并发情况下先查重也会漏，交给数据库报错更安全
        db.rollback()
        raise LabNameDuplicateError(data.name)
    db.refresh(lab)
    return LabResponse.model_validate(lab)


def update_lab(db: Session, data: LabUpdate) -> LabResponse:
    """更新实验室（增量更新，只写传入的字段）"""
    lab = get_lab_or_raise(db, data.id)

    # 名称变更时查重（排除自身）
    if data.name is not None and data.name != lab.name:
        _check_lab_name_unique_or_raise(db, data.name, exclude_id=lab.id)

    # 白名单 + exclude_unset：只更新显式传入的字段，避免主键 / 未传字段被覆盖
    lab_dict = data.model_dump(exclude_unset=True)
    for field, value in lab_dict.items():
        if field in LAB_MUTABLE_FIELDS:
            setattr(lab, field, value)

    # 开放时间可能与原值合并后才合法，因此在赋值后统一校验
    try:
        _validate_open_window(lab.open_time, lab.close_time)
    except LaboratoryException:
        db.rollback()  # 丢弃内存中已改动的字段，保证 Session 干净
        raise

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if data.name is not None:
            raise LabNameDuplicateError(data.name)
        raise LaboratoryException("实验室数据冲突，更新失败", code=409)
    db.refresh(lab)
    return LabResponse.model_validate(lab)


def delete_lab(db: Session, lab_id: int) -> None:
    """删除实验室

    存在预约记录时禁止删除（外键是 RESTRICT，直接删会抛 IntegrityError）；
    实验室下的设备由 cascade="all, delete-orphan" 级联删除。
    """
    lab = get_lab_or_raise(db, lab_id)

    if db.query(LabReservation.id).filter(LabReservation.lab_id == lab_id).first() is not None:
        raise LaboratoryException("该实验室存在预约记录，无法删除", code=409)

    try:
        db.delete(lab)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise LaboratoryException("该实验室存在关联数据，无法删除", code=409)


# ==================================================================
# 二、实验室设备
# ==================================================================
def get_equipment_or_raise(db: Session, equipment_id: int) -> Equipment:
    """获取设备，不存在则抛 EquipmentNotFoundError"""
    equipment = db.get(Equipment, equipment_id)
    if equipment is None:
        raise EquipmentNotFoundError(equipment_id)
    return equipment


def _check_equipment_code_unique_or_raise(
    db: Session, code: str, exclude_id: int | None = None
) -> None:
    """单个设备编号查重"""
    query = db.query(Equipment.id).filter(Equipment.code == code)
    if exclude_id is not None:
        query = query.filter(Equipment.id != exclude_id)
    if query.first() is not None:
        raise EquipmentCodeDuplicateError(code)


def _check_equipment_codes_batch_or_raise(db: Session, codes: list[str]) -> None:
    """批量新增前统一查重：既查数据库，也查同一批次内部是否重复"""
    seen: set[str] = set()
    for code in codes:
        if code in seen:
            raise EquipmentCodeDuplicateError(code)
        seen.add(code)

    if not seen:
        return

    exists = db.query(Equipment.code).filter(Equipment.code.in_(seen)).first()
    if exists is not None:
        raise EquipmentCodeDuplicateError(exists[0])


def _build_equipment_query(data: EquipmentQuery) -> Select:
    """根据条件构造设备查询语句"""
    query = select(Equipment)

    if data.status is not None:
        query = query.where(Equipment.status == data.status)

    if data.lab_ids:
        lab_ids = [lab_id for lab_id in data.lab_ids if lab_id > 0]
        if lab_ids:
            query = query.where(Equipment.lab_id.in_(lab_ids))

    if data.keyword:
        like = f"%{_escape_like(data.keyword)}%"
        query = query.where(
            or_(
                Equipment.name.ilike(like, escape="\\"),
                Equipment.code.ilike(like, escape="\\"),
                Equipment.model.ilike(like, escape="\\"),
            )
        )

    return query


def list_equipments(
    db: Session,
    lab_id: int | None = None,
    *,
    keyword: str | None = None,
    status: int | None = None,
) -> list[EquipmentResponse]:
    """查询设备列表（不分页）；传 lab_id 则只看该实验室，实验室不存在时抛 404"""
    if lab_id is not None:
        get_lab_or_raise(db, lab_id)

    data = EquipmentQuery(
        lab_ids=[lab_id] if lab_id is not None else None,
        keyword=keyword,
        status=status,
    )
    equipments = db.scalars(
        _build_equipment_query(data).order_by(Equipment.id.desc())
    ).all()
    return [EquipmentResponse.model_validate(equipment) for equipment in equipments]


def get_equipment_page(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 10,
    lab_ids: list[int] | None = None,
    keyword: str | None = None,
    status: int | None = None,
) -> PageResponse[EquipmentResponse]:
    """分页查询设备"""
    page, page_size = _normalize_page(page, page_size)
    data = EquipmentQuery(lab_ids=lab_ids, keyword=keyword, status=status)
    total, equipments = _paginate(
        db, _build_equipment_query(data), page, page_size, Equipment.id.desc()
    )
    return PageResponse[EquipmentResponse](
        total=total,
        page=page,
        page_size=page_size,
        data=[EquipmentResponse.model_validate(equipment) for equipment in equipments],
    )


def add_equipments(db: Session, data: EquipmentBatchCreate) -> list[EquipmentResponse]:
    """给指定实验室批量新增设备

    设备状态默认「可用」；实验室可以处于关闭状态（管理员先建库再开放很常见）。
    """
    get_lab_or_raise(db, data.lab_id)
    _check_equipment_codes_batch_or_raise(
        db, [equipment.code for equipment in data.equipments]
    )

    equipments = [
        Equipment(
            name=equipment.name,
            code=equipment.code,
            model=equipment.model,
            description=equipment.description,
            lab_id=data.lab_id,
        )
        for equipment in data.equipments
    ]

    try:
        db.add_all(equipments)
        db.commit()
    except IntegrityError:
        # 并发插入同一编号时唯一索引兜底
        db.rollback()
        raise EquipmentCodeDuplicateError()
    for equipment in equipments:
        db.refresh(equipment)

    return [EquipmentResponse.model_validate(equipment) for equipment in equipments]


def update_equipment(db: Session, data: EquipmentUpdate) -> EquipmentResponse:
    """更新设备（增量更新）"""
    equipment = get_equipment_or_raise(db, data.id)

    if data.code is not None and data.code != equipment.code:
        _check_equipment_code_unique_or_raise(db, data.code, exclude_id=equipment.id)

    equipment_dict = data.model_dump(exclude_unset=True)
    for field, value in equipment_dict.items():
        if field in EQUIPMENT_MUTABLE_FIELDS:
            setattr(equipment, field, value)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if data.code is not None:
            raise EquipmentCodeDuplicateError(data.code)
        raise LaboratoryException("设备数据冲突，更新失败", code=409)
    db.refresh(equipment)
    return EquipmentResponse.model_validate(equipment)


def delete_equipment(db: Session, equipment_id: int) -> None:
    """删除设备"""
    equipment = get_equipment_or_raise(db, equipment_id)
    db.delete(equipment)
    db.commit()


# ==================================================================
# 三、预约校验工具
# ==================================================================
def find_conflicting_reservations(
    db: Session,
    lab_id: int,
    start_time: datetime,
    end_time: datetime,
    *,
    exclude_id: int | None = None,
    statuses: tuple[int, ...] = OCCUPYING_STATUSES,
) -> list[LabReservation]:
    """查询与 [start_time, end_time) 冲突的预约记录

    重叠判定公式（两区间相交的标准写法）：
        新开始 < 旧结束  AND  旧开始 < 新结束

    为什么用严格小于 / 严格大于，而不是 <= / >= ？
        因为区间是半开的 [start, end)：
            旧: 10:00 - 11:00   新: 11:00 - 12:00
            新开始(11:00) < 旧结束(11:00) -> False -> 不算冲突（可以背靠背连排）

    exclude_id: 审核场景排除自身，否则「自己和自己重叠」会导致永远审核不通过。
    返回完整对象（而不是 bool），是为了让上层能提示「被哪些预约占用了」。
    """
    start_time = _normalize_datetime(start_time)
    end_time = _normalize_datetime(end_time)

    query = db.query(LabReservation).filter(
        LabReservation.lab_id == lab_id,
        LabReservation.status.in_(statuses),
        LabReservation.reserve_start_time < end_time,  # 旧开始 < 新结束
        LabReservation.reserve_end_time > start_time,  # 旧结束 > 新开始
    )
    if exclude_id is not None:
        query = query.filter(LabReservation.id != exclude_id)

    return query.order_by(LabReservation.reserve_start_time).all()


def is_time_available(
    db: Session,
    lab_id: int,
    start_time: datetime,
    end_time: datetime,
    *,
    exclude_id: int | None = None,
) -> bool:
    """时段是否可预约（只需要布尔值时用这个）"""
    return not find_conflicting_reservations(
        db, lab_id, start_time, end_time, exclude_id=exclude_id
    )


def is_in_open_time(lab: Lab, start: time, end: time) -> bool:
    """判断时间段是否落在实验室开放时间内（只比时钟，不看日期）

    未配置开放时间 -> 视为全天开放；
    open_time > close_time 表示跨零点开放（如 22:00 - 次日 02:00），
    此时开放区间是 [open, 24:00) ∪ [00:00, close)，
    只要 [start, end) 完整落在其中一段即可。
    """
    if lab.open_time is None or lab.close_time is None:
        return True

    open_time = lab.open_time
    close_time = lab.close_time

    if open_time <= close_time:
        return open_time <= start and end <= close_time

    # 跨零点：[open, 24:00) ∪ [00:00, close)
    return start >= open_time or end <= close_time


def reserve_date_invalid_reason(
    start: datetime, end: datetime, *, now: datetime | None = None
) -> str:
    """预约日期合法性判断，返回不合法原因；返回空字符串表示合法

    规则：必须是同一天、结束时间晚于开始时间、开始时间不能早于当前时间、
    最多提前 MAX_ADVANCE_DAYS 天。返回原因字符串是为了让上层能给用户明确提示。
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
    return ""


def is_valid_reserve_date(
    start: datetime, end: datetime, *, now: datetime | None = None
) -> bool:
    """预约日期是否合法（只需要布尔值时用这个）"""
    return reserve_date_invalid_reason(start, end, now=now) == ""


def check_reserve_date_or_raise(
    start: datetime, end: datetime, *, now: datetime | None = None
) -> None:
    """预约日期不合法时抛 ReservationTimeInvalidError"""
    reason = reserve_date_invalid_reason(start, end, now=now)
    if reason:
        raise ReservationTimeInvalidError(reason)


# ==================================================================
# 四、预约业务
# ==================================================================
def check_reservation_or_raise(db: Session, reservation_id: int) -> LabReservation:
    """获取预约记录，不存在则抛 ReservationNotFoundError"""
    reservation = db.get(LabReservation, reservation_id)
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
) -> ReservationResponse:
    """提交预约申请

    申请人身份由上层传入（登录态 / Agent 调用上下文），本模块不接收前端传来的姓名，
    避免伪造姓名或替别人预约。新申请默认是「待审核」，不占用时段。
    """
    # 1. 申请人必须真实存在（user_id 上有外键，这里提前查一次给出友好提示）
    _check_user_exists_or_raise(db, user_id, label="申请用户")

    # 2. 实验室必须存在且处于开放状态
    lab = check_lab_open_or_raise(db, data.lab_id)

    # 3. 日期合法性：同一天、不在过去、不超过可提前预约天数
    check_reserve_date_or_raise(data.reserve_start_time, data.reserve_end_time)

    # 4. 必须落在开放时间内
    if not is_in_open_time(
        lab, data.reserve_start_time.time(), data.reserve_end_time.time()
    ):
        open_text = (
            f"{lab.open_time:%H:%M}-{lab.close_time:%H:%M}"
            if lab.open_time and lab.close_time
            else "未配置"
        )
        raise ReservationOutsideOpenTimeError(
            f"预约时间不在开放时间内（开放时间: {open_text}）"
        )

    # 5. 人数不能超过实验室容量
    if data.people_num is not None and data.people_num > lab.maxpeople:
        raise ReservationCapacityError(data.people_num, lab.maxpeople)

    # 6. 对实验室行加锁，串行化同一实验室的并发预约，避免 check-then-insert 竞态
    db.query(Lab).filter(Lab.id == data.lab_id).with_for_update().first()

    # 7. 时段冲突检测（只有审核通过的预约占用时段）
    conflicts = find_conflicting_reservations(
        db, data.lab_id, data.reserve_start_time, data.reserve_end_time
    )
    if conflicts:
        # 明细在异常对象里已快照，回滚只会释放行锁，不影响上层读取冲突详情
        error = ReservationTimeConflictError(conflicts)
        db.rollback()
        raise error

    # 8. 落库
    reservation = LabReservation(
        lab_id=lab.id,
        lab_name=lab.name,
        user_id=user_id,
        user_name=user_name,
        purpose=data.purpose,
        people_num=data.people_num,
        reserve_start_time=data.reserve_start_time,
        reserve_end_time=data.reserve_end_time,
    )
    db.add(reservation)
    try:
        db.commit()
    except IntegrityError:
        # 预约单号唯一索引 / user_id 外键兜底（并发场景下先查也会漏，交给数据库报错更安全）
        db.rollback()
        if db.get(User, user_id) is None:
            raise ReservationUserNotFoundError(user_id, label="申请用户")
        raise LaboratoryException("预约单号生成冲突，请重试", code=409)
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
    * 审核通过前再查一次冲突（排除自身），避免同一时段被批成两条成功记录
    * handler_id 只是历史快照（没有外键），但仍然要求处理人真实存在，
      避免写入一个悬空的审批人 id
    """
    reservation = check_reservation_or_raise(db, data.id)

    if reservation.status == RESERVATION_STATUS_CANCELLED:
        raise ReservationStatusError("预约已取消，无法审核")
    if reservation.status == RESERVATION_STATUS_APPROVED:
        raise ReservationStatusError("预约已通过审核，请勿重复操作")

    _check_user_exists_or_raise(db, handler_id, label="处理人(管理员)")

    if data.status == RESERVATION_STATUS_APPROVED:
        # 加锁 + 复检冲突：并发审核同一时段时，只会有第一条成功
        db.query(Lab).filter(Lab.id == reservation.lab_id).with_for_update().first()
        conflicts = find_conflicting_reservations(
            db,
            reservation.lab_id,
            reservation.reserve_start_time,
            reservation.reserve_end_time,
            exclude_id=reservation.id,
        )
        if conflicts:
            error = ReservationTimeConflictError(conflicts)
            db.rollback()  # 释放实验室行锁
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
    * 待审核、已通过的预约都可以取消（取消后自动释放时段）
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
        raise ReservationStatusError(
            f"预约状态取值不合法: {invalid}", code=400
        )
    return normalized


def _build_reservation_query(data: ReservationQuery) -> Select:
    """根据条件构造预约查询语句"""
    query = select(LabReservation)

    if data.user_id is not None:
        query = query.where(LabReservation.user_id == data.user_id)
    if data.handler_id is not None:
        query = query.where(LabReservation.handler_id == data.handler_id)

    if data.lab_ids:
        lab_ids = [lab_id for lab_id in data.lab_ids if lab_id > 0]
        if lab_ids:
            query = query.where(LabReservation.lab_id.in_(lab_ids))

    statuses = _normalize_statuses(data.status)
    if statuses is not None:
        query = query.where(LabReservation.status.in_(statuses))

    if data.keyword:
        like = f"%{_escape_like(data.keyword)}%"
        query = query.where(
            or_(
                LabReservation.reservation_no.ilike(like, escape="\\"),
                LabReservation.lab_name.ilike(like, escape="\\"),
                LabReservation.user_name.ilike(like, escape="\\"),
                LabReservation.purpose.ilike(like, escape="\\"),
            )
        )

    # 时间段筛选：查与 [start_time, end_time) 有交集的预约（与冲突判定同一套区间逻辑）
    if data.start_time is not None:
        query = query.where(
            LabReservation.reserve_end_time > _normalize_datetime(data.start_time)
        )
    if data.end_time is not None:
        query = query.where(
            LabReservation.reserve_start_time < _normalize_datetime(data.end_time)
        )

    return query


def get_reservation_page(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 10,
    status: int | list[int] | None = None,
    lab_ids: list[int] | None = None,
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
        lab_ids=lab_ids,
        user_id=user_id,
        handler_id=handler_id,
        keyword=keyword,
        start_time=start_time,
        end_time=end_time,
    )
    total, rows = _paginate(
        db, _build_reservation_query(data), page, page_size, LabReservation.id.desc()
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
    """当前用户的预约列表"""
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
    lab_ids: list[int] | None = None,
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
        lab_ids=lab_ids,
        keyword=keyword,
    )


def _day_window(lab: Lab, day: date) -> tuple[datetime, datetime]:
    """某实验室在某天的可预约时间窗（同一天内，最晚到 23:59:59.999999）

    跨零点开放（open_time > close_time）时只取当天的 [open, 24:00)：
    预约必须同一天，后半夜那段属于次日的时间窗。
    """
    day_start = datetime.combine(day, time.min)
    day_end = datetime.combine(day, time.max)

    if lab.open_time is None or lab.close_time is None:
        return day_start, day_end

    open_dt = datetime.combine(day, lab.open_time)
    close_dt = datetime.combine(day, lab.close_time)
    if close_dt <= open_dt:
        close_dt = day_end
    return open_dt, close_dt


def get_lab_schedule(
    db: Session,
    lab_id: int,
    day: date | None = None,
    *,
    include_pending: bool = True,
) -> list[ReservationResponse]:
    """查询某实验室某天的预约安排

    include_pending=True（默认）时连待审核的申请一起返回，方便管理员看排队情况；
    False 时只看真正占用时段的「预约成功」记录。
    """
    get_lab_or_raise(db, lab_id)
    day = day or datetime.now().date()
    window_start = datetime.combine(day, time.min)
    window_end = window_start + timedelta(days=1)

    statuses = (
        (RESERVATION_STATUS_PENDING, RESERVATION_STATUS_APPROVED)
        if include_pending
        else OCCUPYING_STATUSES
    )
    rows = find_conflicting_reservations(
        db, lab_id, window_start, window_end, statuses=statuses
    )
    return [ReservationResponse.model_validate(row) for row in rows]


def list_available_slots(
    db: Session,
    lab_id: int,
    day: date | None = None,
    *,
    step_minutes: int = SLOT_STEP_MINUTES,
    now: datetime | None = None,
) -> list[TimeSlot]:
    """列出某实验室某天的可预约时段

    以 step_minutes 为粒度切分开放时间窗，标记每个时段是否被「预约成功」的记录占用；
    已经过去的时间直接从窗口里裁掉，所以第一个时段可能是半截的。
    """
    if step_minutes <= 0 or step_minutes > 24 * 60:
        raise LaboratoryException("时段粒度必须在 1 ~ 1440 分钟之间", code=400)

    lab = check_lab_open_or_raise(db, lab_id)
    now = _normalize_datetime(now) if now is not None else datetime.now()
    day = day or now.date()

    open_dt, close_dt = _day_window(lab, day)
    window_start = max(open_dt, now)
    window_end = close_dt
    if window_start >= window_end:
        return []

    busy = [
        (item.reserve_start_time, item.reserve_end_time)
        for item in find_conflicting_reservations(db, lab_id, window_start, window_end)
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
