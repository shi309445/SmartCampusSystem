"""主链路单测：正常返回、异常路径也打回合汇总、主模型降级

全部用假 runner，不连模型 / 数据库 / Milvus。
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

import app.harness.agent as agent_module
from app.harness.agent import chat
from app.harness.context import AgentContext
from app.observability import spans


class _Runner:
    """最小 runner：有 invoke 就行（chat 只调这一个方法）"""

    def __init__(self, result: dict | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    def invoke(self, *_args, **_kwargs):
        if self.error is not None:
            raise self.error
        return self.result


def _result(answer: str = "正常回答", tokens: int = 12, calls: int = 1) -> dict:
    return {
        "messages": [AIMessage(content=answer)],
        "turn_tokens": tokens,
        "turn_model_calls": calls,
    }


def _capture_finish(monkeypatch: pytest.MonkeyPatch) -> dict:
    captured: dict = {}
    real_finish = spans.finish

    def fake_finish(stats=None, *, status=None):
        captured["stats"] = stats
        return real_finish(stats, status=status)

    monkeypatch.setattr(spans, "finish", fake_finish)
    return captured


def test_chat_returns_answer_and_finishes_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_finish(monkeypatch)

    result = chat("你好", agent=_Runner(_result()), context=AgentContext())

    assert result.answer == "正常回答"
    assert result.tokens == 12
    assert result.model_calls == 1
    assert captured["stats"].status == "ok"
    assert spans.current() is None


def test_chat_finishes_turn_even_when_runner_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_finish(monkeypatch)
    # 没配备用模型：保留原来的抛错行为，由 HTTP 层返回 500/503
    monkeypatch.setattr(agent_module, "get_fallback_model", lambda: None)
    monkeypatch.setattr(agent_module, "fallback_model_name", lambda: None)

    with pytest.raises(RuntimeError):
        chat("你好", agent=_Runner(error=RuntimeError("主模型连接失败")), context=AgentContext())

    assert captured["stats"].status == "error"
    assert spans.current() is None


def test_degraded_streak_counts_and_resets() -> None:
    agent_module._reset_degraded_streak()

    assert agent_module._note_degraded("test:fallback") == 1
    assert agent_module._note_degraded("test:fallback") == 2
    assert agent_module._note_degraded("test:fallback") == 3

    agent_module._reset_degraded_streak()
    assert agent_module._note_degraded("test:fallback") == 1
    agent_module._reset_degraded_streak()


def test_chat_degrades_to_fallback_model(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_finish(monkeypatch)
    monkeypatch.setattr(agent_module, "get_fallback_model", lambda: object())
    monkeypatch.setattr(agent_module, "fallback_model_name", lambda: "test:fallback")
    monkeypatch.setattr(
        agent_module, "build_agent", lambda **_kwargs: _Runner(_result("备用模型回答", 7))
    )

    result = chat(
        "你好",
        agent=_Runner(error=RuntimeError("主模型连接失败")),
        context=AgentContext(),
    )

    assert result.degraded is True
    assert result.fallback_model == "test:fallback"
    assert result.answer == "备用模型回答"
    assert result.tokens == 7

    stats = captured["stats"]
    assert stats.degraded is True
    assert stats.fallback_active is True
