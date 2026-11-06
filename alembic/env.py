"""Alembic 运行环境

**和 `create_all()` 的分工**

    create_all()   启动时用，保证「新库第一次拉起」能跑（fail-open，不阻塞服务）
    alembic        给「已经上线、要改表」的场景用：加列、改索引、数据回填

两者不冲突：`create_all()` 只建**不存在**的表，不会改已存在的表结构；
所以改表必须走 alembic，否则线上库永远不会变。

**连接从哪里来**

默认读 `app.config.settings.DATABASE_URL`（和运行时同一个库），
可以用环境变量 `ALEMBIC_DATABASE_URL` 覆盖 —— 生成初始迁移时要指向一个**空库**：

    $env:ALEMBIC_DATABASE_URL = "mysql+pymysql://root:root@127.0.0.1:3306/scs_migrate?charset=utf8mb4"
    .\\.venv\\Scripts\\python.exe -m alembic revision --autogenerate -m "initial schema"

**为什么要把所有 table 模块 import 一遍**

`Base.metadata` 是「被 import 过的模型」的集合。少 import 一个模块，
autogenerate 就会以为那张表该被删掉，生成一条 `op.drop_table` —— 直接删数据。
所以下面四个 import 不是摆设，新增业务表时也要在这里补一行。

**经常用的命令**

    python -m alembic upgrade head        # 升到最新
    python -m alembic current             # 当前版本
    python -m alembic stamp head          # 只标记版本、不执行 SQL（老库接入时用）
    python -m alembic revision --autogenerate -m "add xxx"
    python -m alembic downgrade -1        # 回退一步
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# 让 `python -m alembic` 从仓库根目录跑时也能 import app.*
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings          # noqa: E402
from app.database import Base            # noqa: E402

# —— 必须 import，才能把表注册进 Base.metadata（见模块文档）——
import app.rag.table                      # noqa: F401,E402
import app.schemas.table                  # noqa: F401,E402
import app.services.laboratory.table      # noqa: F401,E402
import app.services.library.table         # noqa: F401,E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# 允许用环境变量指向临时库（生成初始迁移 / CI 校验用）
DATABASE_URL = os.getenv("ALEMBIC_DATABASE_URL") or settings.DATABASE_URL


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连库（产出交付给 DBA 时用）"""
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连库执行"""
    connectable = create_engine(
        DATABASE_URL,
        poolclass=pool.NullPool,
        connect_args={"charset": "utf8mb4"},
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # 字段类型变了（比如 String(50) -> String(100)）也要能被 autogenerate 发现
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
