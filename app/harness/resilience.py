"""失败处理：工具超时 / 重试 / 熔断 / 并发上限

**六层图里的「失败处理层」三道防线，本模块负责工具侧的两道半**

    ① 重试：只对**幂等读工具**、只对**瞬时故障**，指数退避 + 总预算（本模块）
    ② 降级：主模型 → 备用模型（在 app/harness/model.py + agent.py）
    ③ 熔断 + 兜底：连续失败就停用该工具（本模块）

顺序很重要：**超时 → 不重试**。`TOOL_TIMEOUT` 已经是这次调用的时间预算，
超时还重试只会把学生从 20 秒拖到 60 秒，而幂等读工具的正常耗时是毫秒级 ——
真超时说明对方是真挂了，重试没有意义。重试针对的是**快速失败**：
连接被拒、连接池耗尽、对端重启后的前几百毫秒的 OperationalError。

**为什么工具白名单要硬编码**

`RETRYABLE_TOOL_NAMES` 故意不从 app/tool 里 import，也不读环境变量：
新增工具时**默认不重试**（fail-closed）。写操作重复执行会造成重复下单，
这个风险必须是"显式加白名单才能重试"，不能是"忘了加黑名单就被重试"。

**超时的语义（已确认接受）**

Python 没有安全的方式强杀线程，所以超时是「**线程超时 + 丢弃结果**」：
调用方立刻拿到超时兜底，那条线程会自己跑完再把结果扔掉。因为工具是
「短事务 + 只读为主」，多跑一会儿不会造成副作用。

**为什么要 copy_context**

LangChain 的工具执行发生在中间件 handler 里；把 handler 丢到线程池时，
`contextvars`（trace_id / user_id 这些排障上下文）默认**不会**跟过去。
用 `contextvars.copy_context().run(fn)` 手动带过去，日志里的 trace_id 才不会断。

**为什么工具不是重点，模型才是**

真正的钱和延迟花在模型上，所以模型侧的 timeout / 重试 / 降级在
`app/harness/model.py`（SDK 自带的指数退避）与 `app/harness/agent.py`（备用模型）。
"""

from __future__ import annotations

import contextvars
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any, Callable

import loguru

logger = loguru.logger.bind(name=__name__)

__all__ = [
    "ToolTimeout",
    "ToolBusy",
    "RetryOutcome",
    "CircuitBreaker",
    "circuit_breaker",
    "run_tool_with_timeout",
    "run_tool_with_retry",
    "is_transient_error",
    "is_retryable_tool",
    "breaker_key_for",
    "RETRYABLE_TOOL_NAMES",
]

TOOL_TIMEOUT = float(os.getenv("HARNESS_TOOL_TIMEOUT", "20") or 20)
MAX_CONCURRENT = int(os.getenv("HARNESS_MAX_CONCURRENT_TOOLS", "4") or 4)
ACQUIRE_TIMEOUT = float(os.getenv("HARNESS_TOOL_ACQUIRE_TIMEOUT", "5") or 5)

# 重试参数（见模块文档：只对幂等读工具、只对瞬时故障）
TOOL_RETRY_ENABLED = os.getenv("HARNESS_TOOL_RETRY_ENABLED", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
TOOL_RETRIES = int(os.getenv("HARNESS_TOOL_RETRIES", "2") or 2)
TOOL_BACKOFF_BASE = float(os.getenv("HARNESS_TOOL_BACKOFF_BASE", "0.3") or 0.3)
# 一次工具调用的总时间预算（含所有重试尝试）：比单次 TOOL_TIMEOUT 略大
TOOL_TOTAL_BUDGET = float(os.getenv("HARNESS_TOOL_TOTAL_BUDGET", "25") or 25)
# 剩余预算少于它就不再开新一次尝试
MIN_ATTEMPT_SECONDS = 1.0

# 允许重试的工具（**硬编码**，见模块文档）
RETRYABLE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "retrieval_knowledge_base",
        "search_labs",
        "list_lab_slots",
        "search_study_rooms",
        "find_available_seats",
        "list_my_reservations",
    }
)

# 可重试的异常：按「异常类所在模块」判断，避免在这里 import openai/pymilvus/sqlalchemy，
# 也避免漏掉某个 SDK 自己包装的异常类。
_TRANSIENT_MODULE_PREFIXES: tuple[str, ...] = (
    "sqlalchemy.exc",
    "pymilvus",
    "milvus",
    "httpx",
    "httpcore",
    "urllib3",
    "requests",
    "openai",
    "grpc",
    "redis",
)

# 例外：这些虽然也在 sqlalchemy.exc / openai 里，但重试只会重复同样的错误
_NON_RETRYABLE_NAMES: frozenset[str] = frozenset(
    {
        "IntegrityError",        # 唯一键冲突：重试还是冲突
        "ProgrammingError",      # SQL 写错了
        "DataError",             # 数据超长 / 类型不对
        "NotSupportedError",
        "InvalidRequestError",
        "AuthenticationError",   # 密钥无效
        "PermissionDeniedError",
        "NotFoundError",         # openai 的 404：模型名写错
        "BadRequestError",
    }
)

# 熔断参数
BREAKER_WINDOW = int(os.getenv("HARNESS_BREAKER_WINDOW", "20") or 20)
BREAKER_FAILURE_RATIO = float(os.getenv("HARNESS_BREAKER_FAILURE_RATIO", "0.5") or 0.5)
BREAKER_COOLDOWN = float(os.getenv("HARNESS_BREAKER_COOLDOWN", "60") or 60)


class ToolTimeout(RuntimeError):
    """工具执行超时（结果丢弃）"""


class ToolBusy(RuntimeError):
    """并发的工具调用太多，暂时接不进来"""


class CircuitBreaker:
    """按 key 的滑动窗口熔断器

    每个 key 记最近 window 次成功/失败；失败率 ≥ threshold 就打开 cooldown 秒，
    打开期间 `allow()` 返回 False（调用方直接给兜底话术，不再等超时）。
    """

    def __init__(
        self,
        *,
        window: int = BREAKER_WINDOW,
        failure_ratio: float = BREAKER_FAILURE_RATIO,
        cooldown: float = BREAKER_COOLDOWN,
    ) -> None:
        self.window = max(2, int(window))
        self.failure_ratio = min(1.0, max(0.0, float(failure_ratio)))
        self.cooldown = max(1.0, float(cooldown))
        self._lock = threading.Lock()
        self._events: dict[str, deque[bool]] = {}
        self._open_until: dict[str, float] = {}

    def allow(self, key: str) -> bool:
        with self._lock:
            until = self._open_until.get(key)
            if until is None:
                return True
            if time.monotonic() >= until:
                # 冷却结束：清空窗口重新观察
                self._open_until.pop(key, None)
                self._events.pop(key, None)
                logger.info("熔断恢复：{}", key)
                return True
            return False

    def record(self, key: str, ok: bool) -> None:
        with self._lock:
            events = self._events.setdefault(key, deque(maxlen=self.window))
            events.append(bool(ok))
            if len(events) < self.window:
                return
            failures = sum(1 for event in events if not event)
            if failures / len(events) < self.failure_ratio:
                return
            if key not in self._open_until:
                logger.error(
                    "熔断打开：{}（最近 {} 次失败 {} 次，冷却 {} 秒）",
                    key, len(events), failures, self.cooldown,
                )
            self._open_until[key] = time.monotonic() + self.cooldown

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            return {
                key: {
                    "open": until > now,
                    "remaining_s": round(max(0.0, until - now), 1),
                    "recent": sum(1 for event in self._events.get(key, ()) if not event),
                }
                for key, until in self._open_until.items()
            }


circuit_breaker = CircuitBreaker()

_executor = ThreadPoolExecutor(
    max_workers=max(1, MAX_CONCURRENT), thread_name_prefix="agent-tool"
)
_semaphore = threading.BoundedSemaphore(max(1, MAX_CONCURRENT))


def breaker_key_for(tool_name: str) -> str:
    """把工具名归到三个熔断 key：检索 / 预约 / 其它"""
    if tool_name == "retrieval_knowledge_base":
        return "retrieval"
    if tool_name.startswith(("make_", "cancel_", "list_my_", "search_", "find_", "list_lab")):
        return "reservation"
    return "other"


def run_tool_with_timeout(
    fn: Callable[[], Any],
    *,
    timeout: float | None = None,
    tool_name: str = "",
) -> Any:
    """在受控线程池里执行工具调用

    :raises ToolTimeout: 超时（结果丢弃，线程自己跑完）
    :raises ToolBusy: 并发已满
    :raises Exception: 原样抛出工具自己的异常（由中间件转 ToolMessage）

    contextvars 用 copy_context 显式带过去，否则日志里的 trace_id 会丢。
    """
    limit = TOOL_TIMEOUT if timeout is None else float(timeout)

    if not _semaphore.acquire(timeout=ACQUIRE_TIMEOUT):
        raise ToolBusy(f"工具 {tool_name} 并发已满")

    try:
        context = contextvars.copy_context()
        future = _executor.submit(context.run, fn)
        try:
            return future.result(timeout=limit)
        except FutureTimeoutError as exc:
            future.cancel()                       # 多数情况下杀不掉，结果丢弃
            logger.error("工具 {} 执行超时（{:.1f}s），结果已丢弃", tool_name, limit)
            raise ToolTimeout(f"工具 {tool_name} 执行超时") from exc
    finally:
        _semaphore.release()


# ---------------------------------------------------------------- 重试


@dataclass
class RetryOutcome:
    """一次（可能带重试的）工具调用结果

    `retries` 是**实际发生的重试次数**（0 表示一次就成功），
    中间件拿它写日志 / 回合汇总（FR-1.6）。
    """

    value: Any
    retries: int = 0
    last_error: BaseException | None = None


def is_retryable_tool(tool_name: str) -> bool:
    """这个工具能不能重试（写操作永远返回 False）"""
    return bool(tool_name) and tool_name in RETRYABLE_TOOL_NAMES


def is_transient_error(exc: BaseException) -> bool:
    """异常是不是「重试一次可能就好了」

    判断依据是异常类所在模块 + 类名黑名单，见模块顶部常量。
    `ToolTimeout` / `ToolBusy` 明确返回 False（超时已经花掉预算）。
    """
    if isinstance(exc, (ToolTimeout, ToolBusy)):
        return False

    for cls in type(exc).__mro__:
        name = cls.__name__
        if name in _NON_RETRYABLE_NAMES:
            return False
        module = getattr(cls, "__module__", "") or ""
        if any(module.startswith(prefix) for prefix in _TRANSIENT_MODULE_PREFIXES):
            return True

    # 内置的连接类异常（socket 层）
    return isinstance(exc, (ConnectionError, TimeoutError, OSError)) and not isinstance(
        exc, (FileNotFoundError, PermissionError, IsADirectoryError)
    )


def run_tool_with_retry(
    fn: Callable[[], Any],
    *,
    tool_name: str = "",
    timeout: float | None = None,
    retries: int | None = None,
) -> RetryOutcome:
    """在 `run_tool_with_timeout` 外面套一层「幂等读工具」重试

    :raises ToolTimeout: 每次尝试都可能抛（调用方兜底成错误 ToolMessage）
    :raises ToolBusy: 并发已满
    :raises Exception: 不可重试的异常，或重试耗尽后的最后一次异常

    **不重试的情况**（三种，缺一不可）：
        1. 工具不在白名单（写操作、未知工具）→ fail-closed
        2. 异常不可重试（唯一键冲突、SQL 写错、鉴权失败）
        3. 超时 / 并发满（时间预算已经花光）
    """
    allowed_retries = TOOL_RETRIES if retries is None else int(retries)
    if (
        not TOOL_RETRY_ENABLED
        or allowed_retries <= 0
        or not is_retryable_tool(tool_name)
    ):
        return RetryOutcome(run_tool_with_timeout(fn, timeout=timeout, tool_name=tool_name))

    max_attempts = allowed_retries + 1
    deadline = time.monotonic() + TOOL_TOTAL_BUDGET
    base_timeout = TOOL_TIMEOUT if timeout is None else float(timeout)
    last_error: BaseException | None = None

    for attempt in range(1, max_attempts + 1):
        # 每次尝试的时间上限不能超过总预算剩下的部分
        remaining = deadline - time.monotonic()
        per_attempt = min(base_timeout, max(MIN_ATTEMPT_SECONDS, remaining))
        try:
            value = run_tool_with_timeout(fn, timeout=per_attempt, tool_name=tool_name)
            return RetryOutcome(value, retries=attempt - 1, last_error=last_error)
        except Exception as exc:                       # noqa: BLE001 —— 分类后决定抛还是重试
            last_error = exc
            if attempt >= max_attempts or not is_transient_error(exc):
                raise
            backoff = TOOL_BACKOFF_BASE * (3 ** (attempt - 1))
            remaining = deadline - time.monotonic()
            if remaining <= backoff + MIN_ATTEMPT_SECONDS:
                logger.warning(
                    "工具 {} 第 {} 次失败（{}），剩余预算 {:.1f}s 不足以重试，直接放弃",
                    tool_name, attempt, type(exc).__name__, remaining,
                )
                raise
            logger.warning(
                "工具 {} 第 {} 次失败（{}），{:.1f}s 后重试（{}/{}）",
                tool_name, attempt, type(exc).__name__, backoff,
                attempt, allowed_retries,
            )
            time.sleep(backoff)

    raise last_error if last_error is not None else RuntimeError("工具调用失败")


# ---------------------------------------------------------------- 自检
#
#     .\\.venv\\Scripts\\python.exe -m app.harness.resilience


def _self_check() -> None:  # pragma: no cover - 脚本式自检
    calls = {"n": 0}

    # ① 前两次连接错误、第三次成功 → 重试 2 次后成功
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("connection reset")
        return "ok"

    outcome = run_tool_with_retry(flaky, tool_name="search_labs")
    assert outcome.value == "ok" and outcome.retries == 2, outcome
    logger.info("重试自检：3 次尝试后成功，retries={}", outcome.retries)

    # ② 写工具（不在白名单）不重试
    calls["n"] = 0

    def write_tool():
        calls["n"] += 1
        raise ConnectionError("connection reset")

    try:
        run_tool_with_retry(write_tool, tool_name="make_reservation")
        raise AssertionError("写工具不应该被重试")
    except ConnectionError:
        pass
    assert calls["n"] == 1, f"写工具被调用了 {calls['n']} 次"
    logger.info("重试自检：写工具只尝试 1 次")

    # ③ 不可重试的异常（类名在黑名单里）立即抛出
    class IntegrityError(Exception):        # 故意与 sqlalchemy 同名，测类名黑名单
        pass

    calls["n"] = 0

    def conflict():
        calls["n"] += 1
        raise IntegrityError("duplicate entry")

    try:
        run_tool_with_retry(conflict, tool_name="search_labs")
        raise AssertionError("IntegrityError 不应该被重试")
    except IntegrityError:
        pass
    assert calls["n"] == 1, f"IntegrityError 被尝试了 {calls['n']} 次"
    logger.info("重试自检：不可重试异常只尝试 1 次")

    # ④ 分类函数本身
    assert is_transient_error(ConnectionError("x"))
    assert not is_transient_error(ToolTimeout("x"))
    assert not is_transient_error(IntegrityError("x"))
    assert is_retryable_tool("search_labs")
    assert not is_retryable_tool("make_reservation")
    assert not is_retryable_tool("")

    logger.info("失败处理（重试 / 熔断 / 并发）自检通过")


if __name__ == "__main__":
    _self_check()

