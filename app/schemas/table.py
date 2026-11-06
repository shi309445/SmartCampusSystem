"""全项目通用的表（SQLAlchemy ORM）

存放原则：
    * 被多个业务模块共用的「表」放在本文件：app/schemas/table.py
    * 只服务于某个业务模块的表，放在该模块内部，
      例如实验室业务：app/services/laboratory/table.py

新增表之后，请确认 app/database.py 的 init_db() 已经 import 了对应模块，
否则该表不会进入 Base.metadata，create_all() 也就建不出来。

为什么 users 表放在这里？
    用户是全校园统一的主体：实验室预约要记录申请人 / 审批人，图书馆业务要记录借阅人，
    智能助手的身份上下文也来自登录用户。它不是某一个业务模块的私有数据，
    所以放在全项目通用的 app/schemas/table.py，由 app/services/user 负责业务逻辑。
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, IdMixin

# ====================== 角色与状态常量（避免业务代码里出现魔法字符串） ======================

ROLE_STUDENT = "student"  # 学生
ROLE_ADMIN = "admin"  # 管理员
# 角色取值白名单：只在业务层（Pydantic）校验，数据库不加 CHECK 约束，
# 这样将来新增角色（如 teacher-教师）不需要改表结构
ROLES = (ROLE_STUDENT, ROLE_ADMIN)

USER_STATUS_DISABLED = 0  # 禁用
USER_STATUS_NORMAL = 1  # 正常
USER_STATUSES = (USER_STATUS_DISABLED, USER_STATUS_NORMAL)


class User(Base, IdMixin):
    """用户信息表（全校园统一）"""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN (0, 1)", name="ck_users_status"),
        {"comment": "用户信息表"},
    )

    username: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        index=True,
        comment="账号(学号/工号)",
        nullable=False,
    )
    # 只存 bcrypt 哈希，任何情况下都不落明文密码
    password: Mapped[str] = mapped_column(
        String(255), comment="密码(bcrypt 哈希)", nullable=False
    )
    name: Mapped[str] = mapped_column(String(50), comment="姓名", nullable=False)
    role: Mapped[str] = mapped_column(
        String(20),
        index=True,
        default=ROLE_STUDENT,
        comment="角色: student-学生, admin-管理员",
        nullable=False,
    )
    email: Mapped[str | None] = mapped_column(String(255), comment="邮箱")
    phone: Mapped[str | None] = mapped_column(String(20), comment="手机号")
    avatar: Mapped[str | None] = mapped_column(String(500), comment="头像地址")
    status: Mapped[int] = mapped_column(
        Integer,
        index=True,
        default=USER_STATUS_NORMAL,
        comment="状态: 0-禁用, 1-正常",
        nullable=False,
    )
    last_login_time: Mapped[datetime | None] = mapped_column(
        DateTime, comment="最近登录时间"
    )

    @property
    def is_active(self) -> bool:
        """账号是否可用（未被禁用）"""
        return self.status == USER_STATUS_NORMAL

    @property
    def is_admin(self) -> bool:
        """是否管理员"""
        return self.role == ROLE_ADMIN


class AgentUsage(Base):
    """Agent 用量（预算控制用）：一个用户一天一行

    为什么单独一张表而不是塞进 users：
        用量是「按天累加」的时间序列数据，和用户资料的生命周期不同；单独一张表
        可以只保留最近 N 天、可以按天汇总，也不会因为高频 UPDATE 把 users 行锁住。

    为什么用 MySQL 而不是 Milvus：
        预算扣减要求**原子**（两个并发请求同时扣同一行）。MySQL 的
        `INSERT ... ON DUPLICATE KEY UPDATE col = col + ?` 一条语句就能保证，
        Milvus 没有这种语义。记忆走 Milvus、计费走 MySQL，各用各的长处。
    """

    __tablename__ = "agent_usage"
    __table_args__ = (
        UniqueConstraint("user_id", "usage_date", name="uq_agent_usage_user_date"),
        {"comment": "Agent 用量（日配额）"},
    )

    id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=True, comment="主键ID"
    )
    user_id: Mapped[int] = mapped_column(
        Integer, index=True, nullable=False, comment="用户ID"
    )
    usage_date: Mapped[date] = mapped_column(
        Date, nullable=False, index=True, comment="统计日期"
    )
    tokens: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False, comment="当日 token 用量"
    )
    calls: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="当日模型调用次数"
    )
    steps: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="当日工具/步骤数"
    )
