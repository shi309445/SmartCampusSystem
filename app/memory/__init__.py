"""app.memory —— 记忆层

六层图里「记忆层」目前只落了一档：**短期记忆（会话历史）**，存储是 Milvus
（见 `app/memory/store.py`）。工作记忆留在 agent 的 state 里（`HarnessState`），
长期记忆 / 共享记忆还没有做。

对外用法（`app/harness/agent.py` 的 `chat()` 已经接好，一般不用直接调）：

    from app.memory import load_history, append_messages, make_thread_id

    thread_id = make_thread_id(user_id=7, session_id="web-1")
    history = load_history(thread_id, user_id=7)
    ... 跑一轮 agent ...
    append_messages(thread_id, 7, new_messages)

接口层还用到两个「展示向」的读取函数：

    load_records(thread_id, user_id=...)   原始消息行（带时间 / 是否待确认）
    list_threads(user_id)                  某个学生的会话列表（最近的在前）
"""

from __future__ import annotations

from app.memory.short_term import (
    MEMORY_ENABLED,
    append_messages,
    clear_thread,
    ensure_thread_owner,
    list_threads,
    load_all,
    load_history,
    load_records,
    make_thread_id,
    memory_stats,
    recall,
    replace_tool_result,
    thread_owner,
    thread_session,
    trim_thread,
    wait_for_backfill,
    window_messages,
)

__all__ = [
    "MEMORY_ENABLED",
    "append_messages",
    "clear_thread",
    "ensure_thread_owner",
    "list_threads",
    "load_all",
    "load_history",
    "load_records",
    "make_thread_id",
    "memory_stats",
    "recall",
    "replace_tool_result",
    "thread_owner",
    "thread_session",
    "trim_thread",
    "wait_for_backfill",
    "window_messages",
]
