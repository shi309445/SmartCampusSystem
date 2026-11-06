from sqlalchemy.orm import sessionmaker, DeclarativeBase, Mapped, mapped_column, Session
from sqlalchemy import create_engine, DateTime, func, event
from app.config import settings
from datetime import datetime

engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,   # 默认关闭：SQL 全量回显只在排障时用（.env 里 DB_ECHO=true）
    pool_pre_ping=True,   # 取连接前先探活，避免 MySQL 8 小时断连后的 Lost connection
    pool_recycle=3600,    # 连接最长复用 1 小时
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    connect_args={"connect_timeout": 5,
                  "charset": "utf8mb4",
                  "init_command": "SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci",
                  },
)

@event.listens_for(engine, "connect")
def set_collation(dbapi_conn, connection_record):
    """每个连接建立时强制设置排序规则"""
    with dbapi_conn.cursor() as cursor:
        cursor.execute("SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci")



# session会话的链接工厂
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
    class_=Session,
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class Base(DeclarativeBase):
    """所有表的公共基类：只挂时间戳"""

    create_time: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, server_default=func.now(), comment="创建时间"
    )
    update_time: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now,
        server_default=func.now(), comment="更新时间",
    )


class IdMixin:
    """需要自增主键的表继承它：class User(Base, IdMixin)"""

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, comment="主键ID")


def init_db() -> None:
    """建表；调用前必须确保模型模块已被导入，否则 metadata 是空的"""
    import app.rag.table  # noqa: F401  —— 把模型注册到 Base.metadata
    import app.schemas.table  # noqa: F401  —— 全项目通用的表（users）
    import app.services.laboratory.table  # noqa: F401  —— 实验室业务表（labs/lab_equipments/lab_reservations）
    import app.services.library.table  # noqa: F401  —— 图书馆业务表（library_study_rooms/library_seats/library_reservations）

    Base.metadata.create_all(bind=engine)