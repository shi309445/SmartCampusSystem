"""按 trace 回放的单测：只读 JSONL，不碰网络/模型/数据库"""

from __future__ import annotations

import json
from pathlib import Path

from app.observability import replay


def _line(trace_id: str, timestamp: float, *, span: str, message: str) -> str:
    return json.dumps(
        {
            "record": {
                "time": {"timestamp": timestamp},
                "level": {"name": "INFO"},
                "message": message,
                "extra": {
                    "trace_id": trace_id,
                    "span": span,
                    "latency_ms": 12.5,
                    "tokens_in": 10,
                    "tokens_out": 5,
                    "tokens_total": 15,
                },
            }
        },
        ensure_ascii=False,
    )


def _write_log(directory: Path) -> None:
    lines = [
        _line("b", 2.0, span="turn", message="b 的回合"),
        _line("a", 3.0, span="tool.search_labs", message="工具二"),
        "这是一行坏 JSON",
        _line("a", 1.0, span="model.main", message="模型一"),
        _line("a", 4.0, span="turn", message="a 的回合"),
    ]
    (directory / "app-2026-10-28.jsonl").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def test_load_trace_filters_bad_lines_and_sorts(tmp_path: Path) -> None:
    _write_log(tmp_path)

    records = replay.load_trace("a", tmp_path)

    assert len(records) == 3
    assert [
        (item["record"]["extra"]["span"], item["record"]["time"]["timestamp"])
        for item in records
    ] == [
        ("model.main", 1.0),
        ("tool.search_labs", 3.0),
        ("turn", 4.0),
    ]


def test_load_trace_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert replay.load_trace("nothing", tmp_path / "no-such-dir") == []


def test_format_record_contains_span_and_fields(tmp_path: Path) -> None:
    _write_log(tmp_path)
    record = replay.load_trace("a", tmp_path)[0]

    text = replay.format_record(record)

    assert "model.main" in text
    assert "latency_ms=12.5" in text
    assert "模型一" in text


def test_main_json_output(tmp_path: Path, capsys) -> None:
    _write_log(tmp_path)

    code = replay.main(["a", "--log-dir", str(tmp_path), "--json"])
    out = capsys.readouterr().out

    assert code == 0
    payload = json.loads(out)
    assert len(payload) == 3
    assert payload[-1]["record"]["extra"]["span"] == "turn"


def test_main_missing_trace_returns_one(tmp_path: Path, capsys) -> None:
    _write_log(tmp_path)

    code = replay.main(["missing", "--log-dir", str(tmp_path)])

    assert code == 1
    assert "没有找到" in capsys.readouterr().out
