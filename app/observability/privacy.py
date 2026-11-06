"""追踪隐私：默认不让查询原文离开本机

**为什么需要这个文件**

本地日志已经在 `app/observability/logging.py` 里做 PII 掩码，但 LangSmith 是另一条
出口：它默认会把每条 run 的输入（完整 prompt / 工具结果）和输出上报到云端。
知识库资料里常有联系电话，学生也可能在提问里带手机号 —— 这等于绕过了
需求 2 的 FR-2.3（输出 / 记忆 / 日志不留明文）。

**做法**

只设默认值，不覆盖显式配置：

    LANGSMITH_HIDE_INPUTS=true
    LANGSMITH_HIDE_OUTPUTS=true
    LANGCHAIN_HIDE_INPUTS=true      # 旧版 SDK 的别名
    LANGCHAIN_HIDE_OUTPUTS=true

想临时看完整输入做排障，就在 `.env` 或环境变量里显式设成 `false`；
默认值只在「没配」时生效，不会把排障路堵死。

**关闭追踪本身**

`LANGSMITH_TRACING=false` 仍然是最彻底的做法；本模块只保证「开着追踪时也不上传正文」。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

__all__ = ["HARDENED_ENV", "harden_tracing"]

# 从项目根目录加载 .env：必须在补默认值之前，否则 .env 里显式的 false
# 会被 setdefault 抢先写进去的 true 覆盖（load_dotenv 默认不覆盖已有变量）。
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

# 两套名字：LangSmith 新 SDK 用 LANGSMITH_*，旧版/兼容层可能读 LANGCHAIN_*
HARDENED_ENV: tuple[tuple[str, str], ...] = (
    ("LANGSMITH_HIDE_INPUTS", "true"),
    ("LANGSMITH_HIDE_OUTPUTS", "true"),
    ("LANGCHAIN_HIDE_INPUTS", "true"),
    ("LANGCHAIN_HIDE_OUTPUTS", "true"),
)


def harden_tracing() -> dict[str, str]:
    """把「隐藏输入/输出」的默认值补上，返回最终生效的值

    用 `setdefault` 而不是直接赋值：部署方显式配置的 `false` 必须优先。
    返回 dict 方便自检/测试断言，不参与业务逻辑。
    """
    load_dotenv(_ENV_FILE, override=False)
    applied: dict[str, str] = {}
    for name, value in HARDENED_ENV:
        os.environ.setdefault(name, value)
        applied[name] = os.environ[name]
    return applied
