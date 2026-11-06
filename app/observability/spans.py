"""回合汇总与 span 埋点（可观测性 ① 采集 ⑤ 归因）

**它解决什么问题**

之前一轮问答只留下一串零散日志：想知道「这一轮慢在哪、钱花在哪、有没有降级」，
得把同一个 trace_id 的十几条日志翻出来自己拼。LangSmith 里能看到 span，
但**断网 / 没额度 / 离线排障**时拿不到 —— 而这个项目 90% 的排障是在本地做的。

所以这里补三样，全部落在本地 JSONL 里，不引入 Prometheus：

    ① 回合汇总   一轮**一条** INFO 日志（span=turn），一行看全这一轮
    ② span 埋点  每次工具调用 / 模型调用各一条（span=tool.xxx / span=model.main）
    ③ 回放       app/observability/replay.py 按 trace_id 还原时间线

**为什么用 ContextVar 而不是参数透传**

埋点发生在三个互不相识的地方：`chat()`（agent.py）、中间件（middleware.py）、
工具执行线程（resilience.py 的线程池）。要让它们都往同一个对象里记账，
最省事的是 ContextVar。

**注意：存的是「可变对象」，不是不可变值**

工具在 `contextvars.copy_context().run(...)` 里执行，对 ContextVar 的**重新绑定**
不会传回父上下文；但对同一个对象的**属性修改**会。所以这里
`_current.set(stats)` 只在开轮时做一次，之后全部是 `stats.xxx += 1`。

**关闭方式**

    HARNESS_SPAN_LOG=0    只保留回合汇总，不写工具/模型 span（日志量太大时用）
"""

from __future__ import annotations

import os
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import loguru

logger = loguru.logger.bind(name=__name__)

__all__ = [
    "SPAN_LOG_ENABLED",
    "TurnStats",
    "start_turn",
    "current",
    "set_decision",
    "record_tool",
    "mark_fallback",
    "finish",
]

SPAN_LOG_ENABLED = os.getenv("HARNESS_SPAN_LOG", "1").strip().lower() not in (
    "0", "false", "no", "off",
)

_current: ContextVar["TurnStats | None"] = ContextVar("agent_turn_stats", default=None)


@dataclass
class TurnStats:
    """一轮问答里可累加的观测数据

    只放**chat() 算不出来的东西**：工具次数、重试次数、模型耗时。
    token 总量以预算层（`turn_tokens`）为准，这里不做二次统计。
    """

    trace_id: str | None = None
    user_id: int | None = None
    thread_id: str | None = None

    decision_stage: str | None = None
    allow_rag: bool | None = None

    model_calls: int = 0
    tool_calls: int = 0
    retries: int = 0
    errors: int = 0

    degraded: bool = False
    fallback_model: str | None = None
    # 降级重跑期间置 True：让随后那次模型调用的 span 标成 model.fallback
    fallback_active: bool = False

    tokens_in: int = 0
    tokens_out: int = 0

    status: str = "ok"

    started_at: float = field(default_factory=time.perf_counter)
    started_wall: float = field(default_factory=time.time)
    _model_started: float | None = field(default=None, repr=False)

    # ------------------------------------------------------------------ 耗时
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started_at) * 1000

    def begin_model(self) -> None:
        """模型调用开始（before_model 里调）"""
        self._model_started = time.perf_counter()

    def end_model(self, *, tokens_in: int = 0, tokens_out: int = 0, role: str = "main") -> float:
        """模型调用结束（after_model 里调）：记录耗时与 token，返回耗时毫秒

        role 有三档（FR-3.3）：main / intent / fallback。意图模型自己会调
        `end_model(role="intent")`；降级重跑时调用方先 `mark_fallback()`，
        这里会把主模型那次自动标成 fallback。
        """
        if self._model_started is None:
            return 0.0
        latency_ms = (time.perf_counter() - self._model_started) * 1000
        self._model_started = None

        self.model_calls += 1
        self.tokens_in += max(0, int(tokens_in))
        self.tokens_out += max(0, int(tokens_out))

        effective_role = "fallback" if role == "main" and self.fallback_active else role
        if SPAN_LOG_ENABLED:
            logger.bind(
                span=f"model.{effective_role}",
                latency_ms=round(latency_ms, 1),
                tokens_in=int(tokens_in),
                tokens_out=int(tokens_out),
                degraded=bool(self.degraded or self.fallback_active),
            ).info(
                "模型调用 {:.0f}ms｜tokens {}+{}",
                latency_ms,
                int(tokens_in),
                int(tokens_out),
            )
        return latency_ms

    # ------------------------------------------------------------------ 工具
    def record_tool(
        self,
        tool_name: str,
        latency_ms: float,
        *,
        ok: bool,
        retries: int = 0,
        error_type: str | None = None,
        last_error: str | None = None,
    ) -> None:
        """一次工具调用（含它的重试）记一条 span"""
        self.tool_calls += 1
        self.retries += max(0, int(retries))
        if not ok:
            self.errors += 1

        if SPAN_LOG_ENABLED:
            logger.bind(
                span=f"tool.{tool_name or 'unknown'}",
                latency_ms=round(latency_ms, 1),
                ok=bool(ok),
                retries=int(retries),
                error_type=error_type,
                last_error=last_error,
            ).info(
                "工具调用 {}{}｜{:.0f}ms｜{}",
                tool_name or "unknown",
                f"（重试 {retries} 次）" if retries else "",
                latency_ms,
                "成功" if ok else f"失败({error_type or 'unknown'})",
            )

    # ------------------------------------------------------------------ 汇总
    def summary(self) -> dict[str, Any]:
        return {
            "decision_stage": self.decision_stage,
            "allow_rag": self.allow_rag,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "retries": self.retries,
            "errors": self.errors,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_total": self.tokens_in + self.tokens_out,
            "latency_ms": round(self.elapsed_ms(), 1),
            "degraded": self.degraded,
            "fallback_model": self.fallback_model,
            "status": self.status,
        }


def start_turn(
    trace_id: str | None, user_id: int | None, thread_id: str | None
) -> TurnStats:
    """开一轮：创建统计对象并挂到当前上下文"""
    stats = TurnStats(trace_id=trace_id, user_id=user_id, thread_id=thread_id)
    _current.set(stats)
    return stats


def current() -> TurnStats | None:
    """当前这一轮的统计对象（不在 chat() 里时为 None，埋点直接跳过）"""
    return _current.get()


def set_decision(stage: str | None, allow_rag: bool | None) -> None:
    """记录决策层结论（输入检查中间件里调）"""
    stats = current()
    if stats is None:
        return
    stats.decision_stage = stage
    stats.allow_rag = allow_rag


def record_tool(
    tool_name: str,
    latency_ms: float,
    *,
    ok: bool,
    retries: int = 0,
    error_type: str | None = None,
    last_error: str | None = None,
) -> None:
    """埋点的便捷入口（中间件里调；没有开轮就静默跳过）"""
    stats = current()
    if stats is None:
        return
    stats.record_tool(
        tool_name,
        latency_ms,
        ok=ok,
        retries=retries,
        error_type=error_type,
        last_error=last_error,
    )


def mark_fallback() -> None:
    """标记「接下来这次模型调用是降级重跑」，用于 span 的 role=fallback"""
    stats = current()
    if stats is not None:
        stats.fallback_active = True


def finish(stats: TurnStats | None = None, *, status: str | None = None) -> dict[str, Any]:
    """收尾：打一条回合汇总并清上下文

    放在 `chat()` 的 `finally` 里 —— 异常路径也要有汇总，否则"出问题的那一轮"
    恰好是唯一没有汇总的一轮。
    """
    target = stats or current()
    if target is None:
        return {}
    if status is not None:
        target.status = status

    payload = target.summary()
    logger.bind(span="turn", **payload).info(
        "回合汇总 {}｜决策={}｜模型 {} 次｜工具 {} 次｜重试 {}｜tokens {}+{}｜{:.0f}ms｜降级={}",
        target.status,
        target.decision_stage or "-",
        target.model_calls,
        target.tool_calls,
        target.retries,
        target.tokens_in,
        target.tokens_out,
        payload["latency_ms"],
        target.degraded,
    )
    _current.set(None)
    return payload
