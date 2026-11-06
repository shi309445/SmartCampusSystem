"""预算计量单测：usage 缺失时的估算兜底 + 意图模型用量挂到 DecisionOutcome"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from app.harness import middleware as mw
from app.harness.budget import usage_with_estimate
from app.harness.decision import DecisionOutcome, IntentClassifier, IntentVerdict
from app.harness.decision_rules import RuleVerdict
from app.observability import spans


def test_usage_uses_provider_metadata_when_available() -> None:
    message = AIMessage(
        content="你好",
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )

    usage = usage_with_estimate(message, message.content)

    assert usage.input_tokens == 10
    assert usage.output_tokens == 5
    assert usage.total_tokens == 15


def test_usage_falls_back_to_estimate_when_metadata_missing() -> None:
    message = AIMessage(content="你好" * 100)

    usage = usage_with_estimate(message, message.content)

    assert usage.total_tokens > 0
    assert usage.total_tokens == usage.output_tokens
    assert usage.input_tokens == 0


def test_decision_outcome_carries_intent_tokens() -> None:
    rule = RuleVerdict(action="hit", score=1.0, hit_ratio=1.0, strong=False)
    outcome = DecisionOutcome(
        allow_rag=True,
        stage="intent_confirmed",
        rule=rule,
        intent=IntentVerdict(need_rag=True, tokens_in=10, tokens_out=5),
        hint="",
        query="奖学金怎么申请",
    )

    assert outcome.intent_tokens == 15


def test_decision_without_intent_has_zero_tokens() -> None:
    rule = RuleVerdict(action="hit", score=1.0, hit_ratio=1.0, strong=True)
    outcome = DecisionOutcome(
        allow_rag=True,
        stage="rule_hit",
        rule=rule,
        intent=None,
        hint="",
        query="奖学金怎么申请",
    )

    assert outcome.intent_tokens == 0


def test_input_middleware_skips_intent_when_daily_exhausted(monkeypatch) -> None:
    monkeypatch.setattr(mw.budget, "enabled", True)
    monkeypatch.setattr(mw, "check_daily", lambda _user_id: (False, 300_000, 300))
    monkeypatch.setattr(
        mw,
        "decide",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("超额时不应该再调用意图模型")
        ),
    )

    state = {"messages": [HumanMessage(content="奖学金怎么申请")]}
    runtime = SimpleNamespace(context=SimpleNamespace(user_id=7))

    update = mw.input_and_decision_middleware.before_agent(state, runtime)

    assert update["daily_exceeded"] is True
    assert update["daily_checked"] is True
    assert update["rag_decision"].stage == "budget_exhausted"


def test_input_middleware_records_intent_tokens(monkeypatch) -> None:
    calls: list[tuple] = []

    class _Outcome:
        stage = "intent_confirmed"
        allow_rag = True
        intent = object()
        intent_tokens = 12

    monkeypatch.setattr(mw.budget, "enabled", True)
    monkeypatch.setattr(mw, "check_daily", lambda _user_id: (True, 100, 2))
    monkeypatch.setattr(mw, "decide", lambda *_args, **_kwargs: _Outcome())
    monkeypatch.setattr(mw, "add_daily", lambda *args: calls.append(args))

    state = {"messages": [HumanMessage(content="奖学金怎么申请")]}
    runtime = SimpleNamespace(context=SimpleNamespace(user_id=7))

    update = mw.input_and_decision_middleware.before_agent(state, runtime)

    assert update["turn_tokens"] == 12
    assert update["turn_model_calls"] == 1
    assert calls == [(7, 12, 1)]


def test_intent_classifier_records_model_span() -> None:
    class _Model:
        def invoke(self, _messages, config=None):
            return AIMessage(
                content='{"need_rag": true, "intent": "资助", "confidence": 0.9}',
                usage_metadata={
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                },
            )

    stats = spans.start_turn("trace-intent", user_id=7, thread_id="u7")
    try:
        verdict = IntentClassifier(model=_Model()).classify("学费还没交上怎么办")

        assert verdict.tokens_total == 15
        assert stats.model_calls == 1
        assert stats.tokens_in == 10
        assert stats.tokens_out == 5
    finally:
        spans.finish(stats)
