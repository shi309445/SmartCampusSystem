"""pytest 公共夹具：独立测试库 + 每个用例自动清表 + 常用实体

**为什么用真实 MySQL 而不是 SQLite 内存库**

业务层依赖 MySQL 的行为：`with_for_update()` 行锁、`INSERT ... ON DUPLICATE KEY`、
唯一索引冲突（`IntegrityError`）、`utf8mb4` 排序规则。换 SQLite 会把这些路径全部绕开，
测出来的"通过"没有意义。

**为什么说不会污染开发数据**

    开发库：smart_campus_system       （DATABASE_URL 指的那个）
    测试库：smart_campus_system_test  （本文件按名字派生，可用 TEST_DB_NAME 覆盖）

测试库在会话开始时自动 `CREATE DATABASE IF NOT EXISTS`，结束时 `drop_all()`。
每个用例跑完把**所有表清空**（按外键反序 delete），所以用例之间互不影响。

**怎么跑**

    .\\.venv\\Scripts\\python.exe -m pytest
    .\\.venv\\Scripts\\python.exe -m pytest tests/test_laboratory_rules.py -v
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:                 # 支持直接 `pytest`（不带 -m）时也能 import app.*
    sys.path.insert(0, str(ROOT))

from app.config import settings                              # noqa: E402
from app.database import Base                                # noqa: E402
from app.schemas.table import (                              # noqa: E402
    ROLE_ADMIN,
    ROLE_STUDENT,
    USER_STATUS_NORMAL,
    User,
)
from app.utils.password import hash_password                 # noqa: E402

# 把四张业务表注册进 Base.metadata（顺序无关，import 即注册）
import app.rag.table                     # noqa: F401,E402
import app.schemas.table                 # noqa: F401,E402
import app.services.laboratory.table     # noqa: F401,E402
import app.services.library.table        # noqa: F401,E402

TEST_DB_NAME = os.getenv("TEST_DB_NAME", "smart_campus_system_test")

_url = make_url(settings.DATABASE_URL)
_connect_args = {"charset": "utf8mb4", "connect_timeout": 5}

# 连到「服务器」而不是某个库，用来建测试库
_server_engine = create_engine(_url.set(database=""), connect_args=_connect_args, future=True)
# 测试库引擎：第一次使用时才会真正建连接（那时库已经建好了）
engine = create_engine(_url.set(database=TEST_DB_NAME), connect_args=_connect_args, future=True)
TestingSessionLocal = sessionmaker(
    bind=engine, autoflush=False, expire_on_commit=False, class_=Session
)


# ==================================================================
# 会话级：建库 / 建表 / 拆表
# ==================================================================
@pytest.fixture(scope="session", autouse=True)
def _test_schema() -> None:
    with _server_engine.connect() as conn:
        conn.execute(
            text(
                f"CREATE DATABASE IF NOT EXISTS `{TEST_DB_NAME}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        )
        conn.commit()

    Base.metadata.create_all(bind=engine)
    try:
        yield
    finally:
        Base.metadata.drop_all(bind=engine)
        _server_engine.dispose()
        engine.dispose()


@pytest.fixture()
def db() -> Session:
    """一个用例一个 Session；结束后按外键反序清空所有表"""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()
        session.close()


# ==================================================================
# 实体工厂
# ==================================================================
def make_user(
    db: Session,
    username: str,
    *,
    role: str = ROLE_STUDENT,
    name: str | None = None,
    status: int = USER_STATUS_NORMAL,
    password: str = "Student@123456",
) -> User:
    user = User(
        username=username,
        password=hash_password(password),
        name=name or f"用户{username}",
        role=role,
        status=status,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def at(hour: int, minute: int = 0, *, days: int = 1) -> datetime:
    """今天 + days 天的某个时刻（默认明天，避开"不能预约过去时间"）"""
    return datetime.combine(date.today() + timedelta(days=days), time(hour, minute))


@pytest.fixture()
def student(db: Session) -> User:
    """主测试学生（20210001）"""
    return make_user(db, "20210001", name="张小明")


@pytest.fixture()
def other_student(db: Session) -> User:
    """第二名学生（制造"别人的预约"用）"""
    return make_user(db, "20210002", name="李小红")


@pytest.fixture()
def admin(db: Session) -> User:
    return make_user(db, "admin", role=ROLE_ADMIN, name="王管理")
