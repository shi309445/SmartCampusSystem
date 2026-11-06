"""可观测性/隐私的单测：日志兜底掩码、LangSmith 默认隐藏正文"""

from __future__ import annotations

import os

from app.observability import privacy
from app.observability.logging import _inject_context


def test_log_message_is_masked_before_sink() -> None:
    record = {
        "message": "联系电话 13912345678",
        "extra": {},
        "name": "test",
    }

    _inject_context(record)

    assert "13912345678" not in record["message"]
    assert "139****5678" in record["message"]


def test_log_extra_strings_are_masked() -> None:
    record = {
        "message": "ok",
        "extra": {"note": "邮箱 zhangsan@lut.edu.cn"},
        "name": "test",
    }

    _inject_context(record)

    assert "zhangsan@lut.edu.cn" not in record["extra"]["note"]
    assert record["extra"]["note"].startswith("邮箱 zh***@")


def test_tracing_hides_inputs_and_outputs_by_default(monkeypatch) -> None:
    for name, _ in privacy.HARDENED_ENV:
        monkeypatch.delenv(name, raising=False)

    applied = privacy.harden_tracing()

    assert applied["LANGSMITH_HIDE_INPUTS"] == "true"
    assert applied["LANGSMITH_HIDE_OUTPUTS"] == "true"
    assert os.environ["LANGCHAIN_HIDE_INPUTS"] == "true"
    assert os.environ["LANGCHAIN_HIDE_OUTPUTS"] == "true"


def test_tracing_explicit_false_wins(monkeypatch) -> None:
    monkeypatch.setenv("LANGSMITH_HIDE_INPUTS", "false")

    applied = privacy.harden_tracing()

    assert applied["LANGSMITH_HIDE_INPUTS"] == "false"
