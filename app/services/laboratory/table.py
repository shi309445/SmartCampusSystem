"""实验室业务专用表（SQLAlchemy ORM）

适用范围：只服务实验室业务（实验室、实验室设备、实验室预约），
因此放在业务模块内部 app/services/laboratory/table.py。

如果后续出现多个模块共用的表（例如全校统一的 users 表），
应放到全项目通用的 app/schemas/table.py。

关于申请人 / 处理人：
    全校统一的 users 表在 app/schemas/table.py（业务逻辑在 app/services/user），
    本表对两者的处理刻意不同：
      * user_id（申请人）：加了指向 users.id 的真实外键（ON DELETE RESTRICT）。
        数据库层保证「预约一定挂在一个真实存在的用户上」，同时挡住「还有预约的用户被删掉」
        这类孤儿数据；业务层 create_reservation / delete_user 还会先查一次，给出更友好的提示。
      * handler_id（处理人）：只存历史快照，**故意不加外键**。审批是历史留档，
        管理员离职 / 被删后这个 id 不应该被清空（SET NULL 会丢审计信息），
        姓名另有 handler_name 快照，所以留一个悬空 id 不影响记录可读性。
      * user_name / handler_name：姓名快照，保留「当时」的姓名，用户改名后历史记录仍可读。

已建好的库如果还是没有外键的旧结构，执行一次（表为空时零风险）：
    ALTER TABLE lab_reservations
        ADD CONSTRAINT fk_lab_reservations_user_id
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE RESTRICT;
"""

import random
from datetime import datetime, time

from sqlalchemy import (
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

# 实验室 / 设备的通用状态
STATUS_ABNORMAL = 0  # 关闭 / 禁用 / 不可用
STATUS_NORMAL = 1  # 开放 / 正常 / 可用
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

# 真正「占用」实验室时段的预约状态：
# 只有审核通过的预约才占用时段；待审核的申请不占用（否则谁都能靠刷申请占满时段），
# 但可以提示用户「同一时段已有 N 人在排队」。
OCCUPYING_STATUSES = (RESERVATION_STATUS_APPROVED,)


def generate_reservation_no() -> str:
    """生成预约单号：RES + yyyymmddHHMMSS + 6 位随机数

    示例：RES20250115143012048317
    时间部分 14 位加 6 位随机数，同一秒内也有百万级组合；
    最终唯一性由数据库的 UNIQUE 约束兜底（重复会抛 IntegrityError）。
    """
    return f"RES{datetime.now():%Y%m%d%H%M%S}{random.randint(0, 999999):06d}"


class Lab(Base, IdMixin):
    """实验室信息表"""

    __tablename__ = "labs"
    __table_args__ = (
        CheckConstraint("status IN (0, 1)", name="ck_labs_status"),
        CheckConstraint("maxpeople >= 1", name="ck_labs_maxpeople"),
        {"comment": "实验室信息表"},
    )

    name: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, comment="实验室名称", nullable=False
    )
    location: Mapped[str | None] = mapped_column(String(100), comment="实验室位置")
    maxpeople: Mapped[int] = mapped_column(Integer, comment="最大容纳人数", nullable=False)
    open_time: Mapped[time | None] = mapped_column(Time, comment="开放开始时间")
    close_time: Mapped[time | None] = mapped_column(Time, comment="开放结束时间")
    description: Mapped[str | None] = mapped_column(Text, comment="实验室简介")
    img: Mapped[str | None] = mapped_column(String(200), comment="封面图地址")
    status: Mapped[int] = mapped_column(
        Integer,
        default=STATUS_NORMAL,
        index=True,
        comment="状态: 0-关闭, 1-开放",
        nullable=False,
    )

    # 反向关系：一个实验室有多台设备；删实验室时级联删设备
    equipments: Mapped[list["Equipment"]] = relationship(
        back_populates="lab",
        cascade="all, delete-orphan",
    )


class Equipment(Base, IdMixin):
    """实验室设备信息表"""

    __tablename__ = "lab_equipments"
    __table_args__ = (
        CheckConstraint("status IN (0, 1)", name="ck_lab_equipments_status"),
        {"comment": "实验室设备信息表"},
    )

    name: Mapped[str] = mapped_column(String(50), comment="设备名称", nullable=False)
    code: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, comment="设备编号", nullable=False
    )
    model: Mapped[str | None] = mapped_column(String(100), comment="设备型号")
    description: Mapped[str | None] = mapped_column(Text, comment="设备描述")
    status: Mapped[int] = mapped_column(
        Integer,
        default=STATUS_NORMAL,
        index=True,
        comment="状态: 0-不可用, 1-可用",
        nullable=False,
    )
    lab_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("labs.id", ondelete="CASCADE"),
        index=True,
        comment="所属实验室ID",
        nullable=False,
    )

    # 正向关系：每台设备属于一个实验室
    lab: Mapped["Lab"] = relationship(back_populates="equipments")


class LabReservation(Base, IdMixin):
    """实验室预约事务表"""

    __tablename__ = "lab_reservations"
    __table_args__ = (
        # 状态取值白名单：数据库层兜底，防止写入 99 之类的脏数据
        CheckConstraint("status IN (0, 1, 2, 3)", name="ck_lab_reservations_status"),
        # 时间区间合法性：结束时间必须晚于开始时间（半开区间 [start, end)）
        CheckConstraint(
            "reserve_end_time > reserve_start_time",
            name="ck_lab_reservations_time_range",
        ),
        CheckConstraint(
            "people_num IS NULL OR people_num >= 1",
            name="ck_lab_reservations_people_num",
        ),
        # 冲突查询的覆盖索引：lab_id + status 过滤后再按开始时间比较
        Index(
            "ix_lab_reservations_conflict",
            "lab_id",
            "status",
            "reserve_start_time",
        ),
        {"comment": "实验室预约事务表"},
    )

    # 预约单号：自动生成、不可重复（业务查询用它，主键仍是 Base 里的 id）
    reservation_no: Mapped[str] = mapped_column(
        String(32),
        default=generate_reservation_no,
        unique=True,
        index=True,
        comment="预约单号",
        nullable=False,
    )

    # 实验室：存在预约记录时不允许删除实验室（RESTRICT）
    lab_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("labs.id", ondelete="RESTRICT"),
        index=True,
        comment="实验室ID",
        nullable=False,
    )
    # 冗余存储实验室名称：保留申请时的名称，实验室改名后历史记录仍可读
    lab_name: Mapped[str] = mapped_column(String(50), comment="实验室名称", nullable=False)

    # 申请人：外键指向全校统一的 users 表（RESTRICT：还有预约的用户不允许被删）
    user_id: Mapped[int] = mapped_column(
        Integer,
        # 显式命名：新建库（create_all）与已有库（ALTER TABLE）用同一个约束名，便于排查
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
            name="fk_lab_reservations_user_id",
        ),
        index=True,
        comment="申请人用户ID",
        nullable=False,
    )
    user_name: Mapped[str] = mapped_column(String(50), comment="申请人姓名", nullable=False)

    purpose: Mapped[str | None] = mapped_column(String(500), comment="使用目的/实验内容")
    people_num: Mapped[int | None] = mapped_column(Integer, comment="使用人数")

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
