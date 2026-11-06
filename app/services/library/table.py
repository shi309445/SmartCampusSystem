"""图书馆业务专用表（SQLAlchemy ORM）

适用范围：只服务图书馆业务（自习室、自习室座位、座位预约），
因此放在业务模块内部 app/services/library/table.py，与实验室模块
app/services/laboratory/table.py 的分层方式保持一致。

只有被多个模块共用的表才放全项目通用的 app/schemas/table.py（例如 users 表）。

三张表的对应关系（和实验室模块一一对得上）：
    实验室模块                      图书馆模块
    labs（实验室）           <->    library_study_rooms（自习室）
    lab_equipments（设备）   <->    library_seats（座位）
    lab_reservations（预约） <->    library_reservations（座位预约）
区别在于：实验室预约占的是「整间实验室」，自习室预约占的是「一个座位」，
所以冲突判定按 seat_id 算，同一时段可以有很多人同时预约同一间自习室的不同座位。

关于用户：
    user_id 加了指向 users.id 的真实外键（ON DELETE RESTRICT），
    数据库层保证「预约一定挂在一个真实存在的用户上」，也挡住「还有预约的用户被删掉」；
    handler_id（处理人）只是历史留档快照，故意不加外键——管理员离职 / 被删后
    这个 id 不该被清空（SET NULL 会丢审计信息），姓名另有 handler_name 快照。
"""

import random
from datetime import datetime, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, IdMixin

# ====================== 状态常量（避免业务代码里出现魔法数字） ======================

# 自习室 / 座位的通用状态
STATUS_ABNORMAL = 0  # 关闭 / 停用 / 不可用
STATUS_NORMAL = 1  # 开放 / 可用
STATUSES = (STATUS_ABNORMAL, STATUS_NORMAL)

# 预约状态
RESERVATION_STATUS_PENDING = 0  # 待审核（默认）
RESERVATION_STATUS_APPROVED = 1  # 预约成功
RESERVATION_STATUS_REJECTED = 2  # 预约失败
RESERVATION_STATUS_CANCELLED = 3  # 已取消
RESERVATION_STATUSES = (
    RESERVATION_STATUS_PENDING,
    RESERVATION_STATUS_APPROVED,
    RESERVATION_STATUS_REJECTED,
    RESERVATION_STATUS_CANCELLED,
)

RESERVATION_STATUS_TEXT = {
    RESERVATION_STATUS_PENDING: "待审核",
    RESERVATION_STATUS_APPROVED: "预约成功",
    RESERVATION_STATUS_REJECTED: "预约失败",
    RESERVATION_STATUS_CANCELLED: "已取消",
}

# 真正「占用」座位的预约状态：只有预约成功的记录才占座
OCCUPYING_STATUSES = (RESERVATION_STATUS_APPROVED,)


def generate_reservation_no() -> str:
    """生成预约单号：LIB + yyyymmddHHMMSS + 6 位随机数

    示例：LIB20250115143012048317
    最终唯一性由数据库的 UNIQUE 约束兜底（重复会抛 IntegrityError）。
    """
    return f"LIB{datetime.now():%Y%m%d%H%M%S}{random.randint(0, 999999):06d}"


class StudyRoom(Base, IdMixin):
    """图书馆自习室信息表"""

    __tablename__ = "library_study_rooms"
    __table_args__ = (
        CheckConstraint("status IN (0, 1)", name="ck_library_study_rooms_status"),
        CheckConstraint("capacity >= 1", name="ck_library_study_rooms_capacity"),
        {"comment": "图书馆自习室信息表"},
    )

    name: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, comment="自习室名称", nullable=False
    )
    location: Mapped[str | None] = mapped_column(String(100), comment="位置(楼层/区域)")
    # 座位数上限：新增座位时不允许超过它，保证「座位数 = 可容纳人数」
    capacity: Mapped[int] = mapped_column(Integer, comment="座位数上限", nullable=False)
    open_time: Mapped[time | None] = mapped_column(Time, comment="开放开始时间")
    close_time: Mapped[time | None] = mapped_column(Time, comment="开放结束时间")
    description: Mapped[str | None] = mapped_column(Text, comment="自习室简介")
    img: Mapped[str | None] = mapped_column(String(200), comment="封面图地址")
    status: Mapped[int] = mapped_column(
        Integer,
        default=STATUS_NORMAL,
        index=True,
        comment="状态: 0-关闭, 1-开放",
        nullable=False,
    )

    # 反向关系：一个自习室有多个座位；删自习室时级联删座位
    seats: Mapped[list["Seat"]] = relationship(
        back_populates="room",
        cascade="all, delete-orphan",
    )


class Seat(Base, IdMixin):
    """自习室座位信息表"""

    __tablename__ = "library_seats"
    __table_args__ = (
        CheckConstraint("status IN (0, 1)", name="ck_library_seats_status"),
        {"comment": "自习室座位信息表"},
    )

    code: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, comment="座位编号", nullable=False
    )
    area: Mapped[str | None] = mapped_column(String(50), comment="座位区域(靠窗/静音区等)")
    has_power: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="是否有电源插座", nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, comment="座位描述")
    status: Mapped[int] = mapped_column(
        Integer,
        default=STATUS_NORMAL,
        index=True,
        comment="状态: 0-不可用, 1-可用",
        nullable=False,
    )
    room_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("library_study_rooms.id", ondelete="CASCADE"),
        index=True,
        comment="所属自习室ID",
        nullable=False,
    )

    # 正向关系：每个座位属于一个自习室
    room: Mapped["StudyRoom"] = relationship(back_populates="seats")


class SeatReservation(Base, IdMixin):
    """自习室座位预约事务表"""

    __tablename__ = "library_reservations"
    __table_args__ = (
        # 状态取值白名单：数据库层兜底，防止写入 99 之类的脏数据
        CheckConstraint("status IN (0, 1, 2, 3)", name="ck_library_reservations_status"),
        # 时间区间合法性：结束时间必须晚于开始时间（半开区间 [start, end)）
        CheckConstraint(
            "reserve_end_time > reserve_start_time",
            name="ck_library_reservations_time_range",
        ),
        # 冲突查询覆盖索引：座位占座判定
        Index(
            "ix_library_reservations_seat_conflict",
            "seat_id",
            "status",
            "reserve_start_time",
        ),
        # 同一个人的时间冲突判定（一个人同一时段只能占一个座位）
        Index(
            "ix_library_reservations_user_conflict",
            "user_id",
            "status",
            "reserve_start_time",
        ),
        {"comment": "自习室座位预约事务表"},
    )

    reservation_no: Mapped[str] = mapped_column(
        String(32),
        default=generate_reservation_no,
        unique=True,
        index=True,
        comment="预约单号",
        nullable=False,
    )

    # 自习室：存在预约记录时不允许删除自习室（RESTRICT）
    room_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("library_study_rooms.id", ondelete="RESTRICT"),
        index=True,
        comment="自习室ID",
        nullable=False,
    )
    # 冗余存储名称 / 座位号：保留「下单当时」的信息，改名或换座位后历史记录仍可读
    room_name: Mapped[str] = mapped_column(String(50), comment="自习室名称", nullable=False)

    seat_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("library_seats.id", ondelete="RESTRICT"),
        index=True,
        comment="座位ID",
        nullable=False,
    )
    seat_code: Mapped[str] = mapped_column(String(50), comment="座位编号", nullable=False)

    # 申请人：外键指向全校统一的 users 表（RESTRICT：还有预约的用户不允许被删）
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
            name="fk_library_reservations_user_id",
        ),
        index=True,
        comment="申请人用户ID",
        nullable=False,
    )
    user_name: Mapped[str] = mapped_column(String(50), comment="申请人姓名", nullable=False)

    reserve_start_time: Mapped[datetime] = mapped_column(
        DateTime, comment="预约开始时间", nullable=False
    )
    reserve_end_time: Mapped[datetime] = mapped_column(
        DateTime, comment="预约结束时间", nullable=False
    )

    status: Mapped[int] = mapped_column(
        Integer,
        default=RESERVATION_STATUS_PENDING,
        index=True,
        comment="预约状态: 0-待审核, 1-预约成功, 2-预约失败, 3-已取消",
        nullable=False,
    )

    # 处理说明：默认 NULL，管理员审核时填写（驳回原因 / 通过备注），学生取消时也可写入
    remark: Mapped[str | None] = mapped_column(Text, comment="处理说明")
    # 处理人：历史留档快照，故意不加外键（详见模块 docstring）
    handler_id: Mapped[int | None] = mapped_column(
        Integer, comment="处理人(管理员)用户ID(历史快照, 无外键)"
    )
    handler_name: Mapped[str | None] = mapped_column(String(50), comment="处理人姓名")
    handle_time: Mapped[datetime | None] = mapped_column(DateTime, comment="处理时间")

    @property
    def is_pending(self) -> bool:
        """是否仍处于待审核状态"""
        return self.status == RESERVATION_STATUS_PENDING
