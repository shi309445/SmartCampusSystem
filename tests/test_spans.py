"""回合汇总 / span 单测：字段完整性、token 合计、上下文清理"""

from __future__ import annotations

from loguru import logger

from app.observability import spans


def test_turn_summary_has_required_fields_and_total() -> None:
    stats = spans.start_turn("trace-1", user_id=7, thread_id="u7")
    try:
        stats.begin_model()
        stats.end_model(tokens_in=10, tokens_out=5)
        stats.record_tool("search_labs", 12.5, ok=True, retries=2)
        stats.record_tool("make_reservation", 1.0, ok=False, error_type="ToolTimeout")

        summary = stats.summary()
        assert summary["tokens_in"] == 10
        assert summary["tokens_out"] == 5
        assert summary["tokens_total"] == 15
        assert summary["model_calls"] == 1
        assert summary["tool_calls"] == 2
        assert summary["retries"] == 2
        assert summary["errors"] == 1
        assert summary["status"] == "ok"
    finally:
        spans.finish(stats)

    # 收尾必须把 ContextVar 清掉，否则下一轮会把两轮的数据混在一起
    assert spans.current() is None


def test_mark_fallback_switches_model_span_role() -> None:
    records: list[dict] = []
    handler_id = logger.add(lambda message: records.append(message.record), level="INFO")
    stats = spans.start_turn("trace-2", user_id=None, thread_id=None)
    try:
        spans.mark_fallback()
        assert stats.fallback_active is True

        stats.begin_model()
        stats.end_model(tokens_in=1, tokens_out=2)

        # summary 不直接暴露 role，但 fallback_active 会被 chat() 折算成 degraded
        assert stats.summary()["tokens_total"] == 3
    finally:
        spans.finish(stats)
        logger.remove(handler_id)

    spans_logged = [record["extra"].get("span") for record in records]
    assert "model.fallback" in spans_logged
    assert "model.main" not in spans_logged


def test_finish_accepts_explicit_status() -> None:
    stats = spans.start_turn("trace-3", user_id=None, thread_id=None)
    payload = spans.finish(stats, status="error")

    assert payload["status"] == "error"
    assert spans.current() is None
