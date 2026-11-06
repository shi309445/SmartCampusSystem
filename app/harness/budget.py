"""预算控制：token 计量 + 单轮上限 + 单用户日配额

**六层图里的「预算控制层」只做计数，本模块把计数做全**

    三个计数器：token / 调用次数 / 步骤
    检查点：   每次花钱前必过
    分层配额： 总（进程）/ 主模型（单轮）/ 单工具 / 单用户（按天）
    超标处理： 硬停（用户已定：只硬停，不做降级）

现有 `build_call_limits()` 只数「调用次数」；这里补上**token**（真正的钱），并加
一层「单用户按天配额」。两者分工：

    build_call_limits     次数：单轮最多 6 次模型调用、2 次检索
    budget（本模块）       token：单轮上限 + 今日配额（MySQL 原子扣减）

**计量从哪来**

模型返回的 `AIMessage.usage_metadata`（LangChain 标准字段，DeepSeek 会带回
`input_tokens / output_tokens / total_tokens`）。拿不到就按字符数估：
中文约 1.6 字符 ≈ 1 token，宁可估多不要估少（估少了会漏预算）。

**日配额为什么落 MySQL**

扣减必须原子：两个并发请求同时扣同一行，只有
`INSERT ... ON DUPLICATE KEY UPDATE tokens = tokens + ?` 能保证。记忆走 Milvus、
计费走 MySQL，各用各的长处。表结构见 `app/schemas/table.py::AgentUsage`。

**失败处理：fail-open**

预算保护是「防烧钱」，不该变成「防用」：MySQL 挂了就按没配额限制继续跑并告警
一次；单轮 token 上限不依赖数据库，仍然生效。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import loguru
from sqlalchemy import text

from app.database import SessionLocal
from app.schemas.table import AgentUsage  # noqa: F401 —— 让 create_all 建出这张表

logger = loguru.logger.bind(name=__name__)

__all__ = [
    "BudgetConfig",
    "TokenUsage",
    "budget",
    "extract_usage",
    "estimate_tokens",
    "usage_with_estimate",
    "check_daily",
    "add_daily",
    "get_daily",
    "DAILY_EXCEEDED_TEXT",
    "TURN_EXCEEDED_TEXT",
    "BUDGET_HINT",
]

DAILY_EXCEEDED_TEXT = (
    "今天的智能助手额度已经用完了。如果你还有急事，可以明天再问，"
    "或直接联系相关职能部门。"
)
TURN_EXCEEDED_TEXT = (
    "这个问题需要的资料比较多，这一轮先到这里。"
    "可以把问题拆小一点再问，或者补充你最关心的那个点。"
)
BUDGET_HINT = (
    "【预算提示】本轮已经用了不少资料，请尽快收敛：直接给出结论，"
    "不要再展开无关细节，也不要再调用检索。"
)


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.getenv(name)
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


@dataclass
class BudgetConfig:
    """预算参数（全部走环境变量，不配就用默认值）"""

    enabled: bool = field(default_factory=lambda: _flag("HARNESS_BUDGET_ENABLED", True))
    # 单轮（一次提问）token 硬上限：6 万 ≈ 一次很长的多轮检索
    turn_tokens: int = field(default_factory=lambda: _env_int("HARNESS_TURN_TOKENS", 60_000))
    # 接近上限时注入「收敛」提示的比例
    soft_ratio: float = field(default_factory=lambda: _env_float("HARNESS_TURN_SOFT_RATIO", 0.8))
    # 单用户每日 token / 调用次数（<=0 表示不限）
    daily_tokens: int = field(default_factory=lambda: _env_int("HARNESS_DAILY_TOKENS", 300_000))
    daily_calls: int = field(default_factory=lambda: _env_int("HARNESS_DAILY_CALLS", 300))


budget = BudgetConfig()


@dataclass
class TokenUsage:
    """一次模型调用的 token 用量"""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def __bool__(self) -> bool:
        return self.total_tokens > 0


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def extract_usage(message: Any) -> TokenUsage:
    """从 AIMessage 里取 token 用量（拿不到就返回 0，不抛异常）"""
    meta = getattr(message, "usage_metadata", None)
    if isinstance(meta, dict):
        input_tokens = _to_int(meta.get("input_tokens"))
        output_tokens = _to_int(meta.get("output_tokens"))
        total = _to_int(meta.get("total_tokens")) or input_tokens + output_tokens
        if total:
            return TokenUsage(input_tokens, output_tokens, total)

    # 兜底：部分 provider 只把原始 usage 放在 response_metadata 里
    response_meta = getattr(message, "response_metadata", None)
    if isinstance(response_meta, dict):
        raw = response_meta.get("token_usage") or response_meta.get("usage") or {}
        if isinstance(raw, dict):
            input_tokens = _to_int(raw.get("prompt_tokens") or raw.get("input_tokens"))
            output_tokens = _to_int(raw.get("completion_tokens") or raw.get("output_tokens"))
            total = _to_int(raw.get("total_tokens")) or input_tokens + output_tokens
            if total:
                return TokenUsage(input_tokens, output_tokens, total)
    return TokenUsage()


def estimate_tokens(text: str) -> int:
    """没有 usage 时按字符数估 token（中文约 1.6 字符/token，向上取整）"""
    if not text:
        return 0
    return max(1, int(len(text) / 1.6) + 1)


def usage_with_estimate(message: Any, text: str = "") -> TokenUsage:
    """优先用 provider 的 usage；拿不到就按输出文本估一个下限

    为什么必须有这个兜底：部分 provider / 代理不回 `usage_metadata`，只靠
    `extract_usage` 会记 0 token —— 单轮上限和日配额都会直接失效（相当于
    预算层对这类模型完全不起作用）。估多比漏记安全，所以这里宁可高估。
    """
    usage = extract_usage(message)
    if usage.total_tokens > 0:
        return usage
    estimated = estimate_tokens(text)
    return TokenUsage(input_tokens=0, output_tokens=estimated, total_tokens=estimated)


# ---------------------------------------------------------------- 日配额（MySQL）


def _today() -> date:
    return date.today()


def get_daily(user_id: int | None) -> tuple[int, int]:
    """读今天的用量：(tokens, calls)。没登录 / 数据库不可用时返回 (0, 0)"""
    if user_id is None:
        return 0, 0
    try:
        with SessionLocal() as db:
            row = db.execute(
                text(
                    "SELECT tokens, calls FROM agent_usage "
                    "WHERE user_id = :user_id AND usage_date = :day"
                ),
                {"user_id": int(user_id), "day": _today()},
            ).first()
    except Exception as exc:                      # noqa: BLE001 —— 预算不能挡着问答
        logger.warning("读取日配额失败（按不限处理）：{}", exc)
        return 0, 0
    if row is None:
        return 0, 0
    return _to_int(row[0]), _to_int(row[1])


def check_daily(user_id: int | None) -> tuple[bool, int, int]:
    """今天还能不能用：返回 (是否放行, 已用 tokens, 已用 calls)"""
    if not budget.enabled or user_id is None:
        return True, 0, 0

    tokens, calls = get_daily(user_id)
    if budget.daily_tokens > 0 and tokens >= budget.daily_tokens:
        logger.warning("日配额用尽（token {} >= {}）：user_id={}", tokens, budget.daily_tokens, user_id)
        return False, tokens, calls
    if budget.daily_calls > 0 and calls >= budget.daily_calls:
        logger.warning("日配额用尽（调用 {} >= {}）：user_id={}", calls, budget.daily_calls, user_id)
        return False, tokens, calls
    return True, tokens, calls


def add_daily(user_id: int | None, tokens: int, calls: int = 1, steps: int = 0) -> None:
    """原子累加当天用量（一条 UPSERT 解决并发）"""
    if not budget.enabled or user_id is None:
        return
    if tokens <= 0 and calls <= 0 and steps <= 0:
        return
    try:
        with SessionLocal() as db:
            db.execute(
                text(
                    "INSERT INTO agent_usage "
                    "(user_id, usage_date, tokens, calls, steps, create_time, update_time) "
                    "VALUES (:user_id, :day, :tokens, :calls, :steps, NOW(), NOW()) "
                    "ON DUPLICATE KEY UPDATE "
                    "tokens = tokens + VALUES(tokens), "
                    "calls = calls + VALUES(calls), "
                    "steps = steps + VALUES(steps), "
                    "update_time = NOW()"
                ),
                {
                    "user_id": int(user_id),
                    "day": _today(),
                    "tokens": int(tokens),
                    "calls": int(calls),
                    "steps": int(steps),
                },
            )
            db.commit()
    except Exception as exc:                      # noqa: BLE001
        logger.warning("累加日配额失败（本次不记账）：{}", exc)
