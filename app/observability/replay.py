"""按 trace_id 回放一轮问答的日志时间线（可观测性 ③ 回放）

    .\\.venv\\Scripts\\python.exe -m app.observability.replay 7fbe7ae8b7c34013
    .\\.venv\\Scripts\\python.exe -m app.observability.replay 7fbe7ae8b7c34013 --json
    .\\.venv\\Scripts\\python.exe -m app.observability.replay 7fbe7ae8b7c34013 --log-dir logs/archive

**它回答的问题**

    「这一轮为什么慢 / 花了多少钱 / 卡在哪一步 / 是不是降级了」

**为什么是回放日志，不是重放模型**

重跑一遍要重新花钱、而且结果不会一样（模型非确定性），排障价值反而低。
这里只做**只读回放**：把同一个 trace_id 的日志按时间排好、整理成时间线。
断网、没额度、线下排障时都能用 —— 这正是它相对 LangSmith 的意义。

**时间线长什么样**

    13:26:48.101 | INFO    | turn                   | 回合汇总 decision_stage=rule_hit ...
    13:26:48.121 | INFO    | model.main             | 模型调用 latency_ms=812.3 tokens_in=1204 ...
    13:26:48.940 | INFO    | tool.retrieval_knowledge_base | 工具调用 latency_ms=803.2 ok=True ...

（按时间升序；`turn` 汇总行在最后，因为它是在 finally 里打的。）
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from app.observability.logging import LOG_DIR

__all__ = ["load_trace", "format_record", "main"]

# 这几个字段在表头已经展示过，不用逐行重复
_HEADER_KEYS = frozenset({"trace_id", "span", "name"})
_SPAN_WIDTH = 26


def _iter_files(log_dir: Path) -> Iterable[Path]:
    """按文件名（日期）升序，跨天轮转的文件也能连起来读"""
    return sorted(log_dir.glob("app-*.jsonl"))


def load_trace(trace_id: str, log_dir: Path | str = LOG_DIR) -> list[dict[str, Any]]:
    """取出某个 trace_id 的全部日志记录（按时间升序）

    坏行直接跳过：日志是排障用的，不能因为一行写坏了就让整个回放失败。
    """
    target = Path(log_dir)
    if not target.exists():
        return []

    records: list[dict[str, Any]] = []
    for path in _iter_files(target):
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                record = payload.get("record") or {}
                extra = record.get("extra") or {}
                if str(extra.get("trace_id") or "") != str(trace_id):
                    continue
                records.append(payload)

    records.sort(key=lambda item: ((item.get("record") or {}).get("time") or {}).get("timestamp") or 0)
    return records


def _timestamp(payload: dict[str, Any]) -> float:
    return float(((payload.get("record") or {}).get("time") or {}).get("timestamp") or 0)


def _clock(payload: dict[str, Any]) -> str:
    try:
        return datetime.fromtimestamp(_timestamp(payload)).strftime("%H:%M:%S.%f")[:-3]
    except (OSError, ValueError):
        return "--:--:--.---"


def format_record(payload: dict[str, Any]) -> str:
    """一条日志 → 一行可读文本"""
    record = payload.get("record") or {}
    extra = record.get("extra") or {}
    level = str((record.get("level") or {}).get("name") or "INFO")
    span = str(extra.get("span") or "-")
    message = str(record.get("message") or "")

    fields = " ".join(
        f"{key}={value}"
        for key, value in extra.items()
        if key not in _HEADER_KEYS and value not in (None, "", "-")
    )
    line = f"{_clock(payload)} | {level:<7} | {span:<{_SPAN_WIDTH}} | {message}"
    if fields:
        line = f"{line} {fields}"
    return line.rstrip()


def _header(records: list[dict[str, Any]]) -> str:
    for payload in records:
        extra = (payload.get("record") or {}).get("extra") or {}
        if extra.get("span") == "turn" or extra.get("user_id") is not None:
            parts = [
                f"trace={extra.get('trace_id') or '-'}",
                f"user={extra.get('user_id') or '-'}",
                f"thread={extra.get('thread_id') or '-'}",
            ]
            return "  ".join(parts)
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="按 trace_id 回放一轮问答的日志时间线（只读，不重跑模型）"
    )
    parser.add_argument("trace_id", help="响应头 X-Trace-Id 里的那个 id")
    parser.add_argument("--log-dir", default=str(LOG_DIR), help="日志目录（默认 logs/）")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON，便于贴到 issue 里")
    args = parser.parse_args(argv)

    records = load_trace(args.trace_id, args.log_dir)
    if not records:
        print(f"没有找到 trace_id={args.trace_id} 的日志（目录：{args.log_dir}）")
        print("提示：trace_id 在响应头 X-Trace-Id 里，或 /api/chat 返回体的 trace_id 字段。")
        return 1

    if args.json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return 0

    print(f"共 {len(records)} 条日志")
    header = _header(records)
    if header:
        print(header)
    print("-" * 100)
    for payload in records:
        print(format_record(payload))
    print("-" * 100)

    # 有回合汇总就把关键指标再单独列一遍，省得人肉从字段里挑
    summary = next(
        (
            (payload.get("record") or {}).get("extra") or {}
            for payload in reversed(records)
            if ((payload.get("record") or {}).get("extra") or {}).get("span") == "turn"
        ),
        None,
    )
    if summary:
        print(
            "本回合："
            f"决策={summary.get('decision_stage')}｜"
            f"模型调用={summary.get('model_calls')}｜"
            f"工具调用={summary.get('tool_calls')}｜"
            f"重试={summary.get('retries')}｜"
            f"tokens={int(summary.get('tokens_in') or 0)}+{int(summary.get('tokens_out') or 0)}"
            f"={int(summary.get('tokens_total') or 0)}｜"
            f"耗时={summary.get('latency_ms')}ms｜"
            f"降级={summary.get('degraded')}｜"
            f"状态={summary.get('status')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
