"""失败处理规则单测：重试白名单 / 超时不重试 / 熔断记账

这些用例刻意不碰数据库、不碰模型：把 `run_tool_with_timeout` 换成直接调用，
只验证 resilience.py 自己那层"该不该重试、重试几次"的判断。
"""

from __future__ import annotations

import pytest

from app.harness import resilience


def test_only_read_tools_are_retryable() -> None:
    assert resilience.is_retryable_tool("search_labs")
    assert resilience.is_retryable_tool("retrieval_knowledge_base")
    assert not resilience.is_retryable_tool("make_reservation")
    assert not resilience.is_retryable_tool("cancel_my_reservation")
    assert not resilience.is_retryable_tool("")


def test_transient_error_is_retried_and_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("connection reset")
        return "ok"

    # 不真的开线程池 / 不真的 sleep：只验证重试判定与计数
    monkeypatch.setattr(resilience, "run_tool_with_timeout", lambda fn, **_: fn())
    monkeypatch.setattr(resilience, "TOOL_RETRY_ENABLED", True)
    monkeypatch.setattr(resilience, "TOOL_RETRIES", 2)
    monkeypatch.setattr(resilience, "TOOL_BACKOFF_BASE", 0.0)
    monkeypatch.setattr(resilience.time, "sleep", lambda _seconds: None)

    outcome = resilience.run_tool_with_retry(flaky, tool_name="search_labs")

    assert outcome.value == "ok"
    assert outcome.retries == 2
    assert len(calls) == 3


def test_write_tool_is_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def write_tool() -> None:
        calls.append(1)
        raise ConnectionError("connection reset")

    monkeypatch.setattr(resilience, "run_tool_with_timeout", lambda fn, **_: fn())

    with pytest.raises(ConnectionError):
        resilience.run_tool_with_retry(write_tool, tool_name="make_reservation")

    assert len(calls) == 1


def test_timeout_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def timeout_tool() -> None:
        calls.append(1)
        raise resilience.ToolTimeout("tool timeout")

    monkeypatch.setattr(resilience, "run_tool_with_timeout", lambda fn, **_: fn())

    with pytest.raises(resilience.ToolTimeout):
        resilience.run_tool_with_retry(timeout_tool, tool_name="search_labs")

    assert len(calls) == 1


def test_circuit_breaker_opens_after_failure_ratio() -> None:
    breaker = resilience.CircuitBreaker(window=2, failure_ratio=0.5, cooldown=60)
    assert breaker.allow("reservation")

    breaker.record("reservation", False)          # 1/1，还没到窗口
    assert breaker.allow("reservation")

    breaker.record("reservation", False)          # 2/2 → 打开
    assert not breaker.allow("reservation")
    assert breaker.snapshot()["reservation"]["open"] is True


def test_error_classification() -> None:
    assert resilience.is_transient_error(ConnectionError("x"))
    assert not resilience.is_transient_error(resilience.ToolTimeout("x"))
    assert not resilience.is_transient_error(resilience.ToolBusy("x"))

    class IntegrityError(Exception):               # 类名黑名单，模拟 sqlalchemy
        pass

    assert not resilience.is_transient_error(IntegrityError("duplicate"))
