"""日志：控制台 + 结构化 JSONL 落盘，并自动带上 trace_id

**三件事**

    ① 落盘：logs/app-{日期}.jsonl（一行一个 JSON），rotation 20MB / 保留 14 天
    ② 带上上下文：patcher 把 trace_id / user_id / thread_id 塞进每条日志的 extra
    ③ 幂等：模块内标志位，重复调用只生效一次（CLI / FastAPI lifespan 都能安全调）

**为什么用 JSONL 而不是上 Prometheus**

生产必需档要求的是「采集 + 查看」：出事能查到。一行 JSON 里已经有
`trace_id / span / latency_ms / tokens / status`，用 `jq`、Loki、甚至 `findstr`
都能聚合；Prometheus 属于下一档（进阶），现在上就是过早复杂。

**enqueue 的降级**

loguru 的 `enqueue=True` 底层是 multiprocessing 命名管道，某些受限环境（容器 /
沙箱）会直接 PermissionError。日志不能因此消失：加 handler 失败就自动退回同步写。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import loguru

from app.config import BASE_DIR
from app.utils.guard import mask_pii
from app.utils.trace import get_trace_context

__all__ = ["setup_logging", "LOG_DIR"]

# 用项目根目录而不是 CWD：服务从别的目录启动时日志也不会丢到意外位置
LOG_DIR = Path(BASE_DIR) / "logs"

_configured = False

CONSOLE_FORMAT = (
    "<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <7}</level> | "
    "<cyan>{extra[trace_id]}</cyan> | <level>{message}</level>"
)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _inject_context(record: dict[str, Any]) -> None:
    """给每条日志挂上当前请求的上下文（已 bind 的值优先，不覆盖）"""
    context = get_trace_context()
    extra = record["extra"]
    extra.setdefault("trace_id", context.get("trace_id") or "-")
    if context.get("user_id") is not None:
        extra.setdefault("user_id", context["user_id"])
    if context.get("thread_id"):
        extra.setdefault("thread_id", context["thread_id"])
    if "name" not in extra:
        extra["name"] = record.get("name")

    # 最后一道保险：无论调用点有没有记得脱敏，落到控制台/JSONL 之前都再过一遍。
    # 只处理字符串，trace_id / span / name 这些不会命中 PII 规则，代价可以忽略。
    record["message"] = mask_pii(str(record.get("message") or ""))
    for key, value in list(extra.items()):
        if isinstance(value, str):
            extra[key] = mask_pii(value)


def _add_jsonl_handler(target: Path, level: str, enqueue: bool) -> int:
    """加文件 handler；enqueue 在当前环境不可用时自动退回同步写"""
    filename = target / "app-{time:YYYY-MM-DD}.jsonl"
    options: dict[str, Any] = dict(
        level=level,
        serialize=True,              # 整个 record 序列化成一行 JSON
        rotation="20 MB",
        retention="14 days",
        backtrace=True,
        diagnose=False,              # 不把变量值打进日志（可能含学生隐私）
    )
    try:
        return loguru.logger.add(filename, enqueue=enqueue, **options)
    except (PermissionError, OSError) as exc:
        if not enqueue:
            raise
        loguru.logger.warning("日志 enqueue 不可用，改为同步写入：{}", exc)
        return loguru.logger.add(filename, enqueue=False, **options)


def setup_logging(
    *,
    log_dir: str | Path | None = None,
    level: str = "INFO",
    console: bool = True,
    jsonl: bool = True,
    enqueue: bool | None = None,
) -> Path:
    """初始化日志；返回 JSONL 目录（收不到日志时是空目录）"""
    global _configured
    target = Path(log_dir) if log_dir else LOG_DIR
    if _configured:
        return target

    use_enqueue = _env_flag("LOG_ENQUEUE", True) if enqueue is None else enqueue

    loguru.logger.remove()
    loguru.logger.configure(patcher=_inject_context)

    if console:
        loguru.logger.add(sys.stderr, level=level, format=CONSOLE_FORMAT, colorize=True)

    if jsonl:
        try:
            target.mkdir(parents=True, exist_ok=True)
            _add_jsonl_handler(target, level, use_enqueue)
        except OSError as exc:            # 只读目录 / 权限不足：只影响落盘
            loguru.logger.warning("日志落盘不可用（仅控制台输出）：{}", exc)

    _configured = True
    return target
