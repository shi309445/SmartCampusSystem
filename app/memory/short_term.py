"""短期记忆：会话历史（消息行）的读写 + 窗口化 + 会话身份

**它解决的问题**

`app/harness/agent.py` 之前把多轮历史完全交给调用方：Web 层每轮自己拼 history，
服务一重启就失忆，也没有「只带最近几轮」的窗口管理。这个模块把两件事收进来：

    1. **持久化**：每轮产生的消息（用户 / AI / 工具结果）落 Milvus（见 store.py），
       下次同一 thread_id 进来时按 seq 顺序读回；
    2. **窗口化**：只把最近 N 轮交给模型，防止上下文越滚越大（六层图里的
       「短期记忆：滑动窗口 / 摘要，防撑爆上下文」）。

**thread_id 与 user_id 的绑定（硬约束）**

    thread_id = "u{user_id}"                    # 一个学生一条长会话
    thread_id = "u{user_id}:{session_id}"       # 一个学生多条会话（前端传 session_id）

所以 `thread_owner("u7:web-1") == 7`。任何读写入口都可以带 `user_id` 做二次校验：
**别人的 thread_id 读不出来、也写不进去**，防止前端传一个 id 就翻到别人的会话。

**为什么先写零向量、再异步补**

第一次写记忆时如果同步加载 bge-m3（约 6~10 秒），学生问「你好」也要干等。
所以写入路径只做一次 upsert（快），把「embedding + 第二次 upsert」丢给一个
后台守护线程（一次一批，进程退出时最多丢一批的向量，行本身已经落库）。
`recall()` 只认 `embedded == true` 的行，所以「语义召回暂时不可用」不会污染结果。

**自检**

    .\\.venv\\Scripts\\python.exe -m app.memory.short_term
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from datetime import datetime
from typing import Any, Iterable, Sequence

import loguru
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.memory.store import VECTOR_DIM, get_store
from app.utils.guard import mask_payload, mask_pii

logger = loguru.logger.bind(name=__name__)

__all__ = [
    "MEMORY_ENABLED",
    "make_thread_id",
    "thread_owner",
    "thread_session",
    "ensure_thread_owner",
    "window_messages",
    "load_history",
    "load_all",
    "load_records",
    "list_threads",
    "append_messages",
    "replace_tool_result",
    "recall",
    "clear_thread",
    "trim_thread",
    "memory_stats",
    "wait_for_backfill",
]

# ---------------------------------------------------------------- 配置

MEMORY_ENABLED = os.getenv("MEMORY_DISABLED", "0").strip().lower() not in ("1", "true", "yes", "on")
WINDOW_TURNS = int(os.getenv("MEMORY_WINDOW_TURNS", "8") or 8)
WINDOW_MESSAGES = int(os.getenv("MEMORY_WINDOW_MESSAGES", "40") or 40)
MAX_MESSAGES_PER_THREAD = int(os.getenv("MEMORY_MAX_MESSAGES", "400") or 400)
MAX_CONTENT_CHARS = int(os.getenv("MEMORY_MAX_CONTENT_CHARS", "6000") or 6000)
EMBED_MESSAGES = os.getenv("MEMORY_EMBED", "1").strip().lower() not in ("0", "false", "no", "off")

TRUNCATED_NOTE = "…（内容过长，记忆里已截断）"

# seq 是「读已有最大 seq + 1」推出来的，同一会话的两次并发写会撞成同一个 pk
# （upsert 会覆盖掉一条消息）。单进程里用一把锁串行化就够；多进程部署时
# 需要换成「分布式序号」或把会话绑到固定 worker。
_append_lock = threading.Lock()

# ---------------------------------------------------------------- 会话身份

_THREAD_RE = re.compile(r"^u(?P<user>\d+)(?::(?P<session>[0-9A-Za-z_\-]{1,64}))?$")
_UNSAFE_SESSION = re.compile(r"[^0-9A-Za-z_\-]")


def make_thread_id(user_id: int, session_id: str | None = None) -> str:
    """由用户 id（+ 可选的会话 id）生成 thread_id

    `thread_id` 里**必须**能反解出 user_id —— 这是「thread_id 与用户绑定」的落点。
    前端想把一个学生的对话分成多个会话，就把 session_id 传进来；不传就是一条长会话。
    """
    if user_id is None:
        raise ValueError("生成 thread_id 需要 user_id")
    base = f"u{int(user_id)}"
    if not session_id:
        return base
    safe = _UNSAFE_SESSION.sub("", str(session_id))[:64] or "default"
    return f"{base}:{safe}"


def thread_owner(thread_id: str | None) -> int | None:
    """从 thread_id 反解 user_id；不是本模块生成的格式就返回 None"""
    if not thread_id:
        return None
    match = _THREAD_RE.match(str(thread_id))
    return int(match.group("user")) if match else None


def thread_session(thread_id: str | None) -> str | None:
    if not thread_id:
        return None
    match = _THREAD_RE.match(str(thread_id))
    return match.group("session") if match else None


def ensure_thread_owner(thread_id: str, user_id: int | None) -> None:
    """校验 thread_id 属于这个用户；不属于就抛 PermissionError

    调用点（`load_history` / `confirm_action`）都必须过这一步：只要 thread_id 能被
    外部指定，就必须验证它属于当前登录用户。
    """
    if user_id is None:
        raise PermissionError("匿名用户不能访问会话记忆")
    owner = thread_owner(thread_id)
    if owner is None or owner != int(user_id):
        raise PermissionError(f"thread_id {thread_id!r} 不属于当前用户")


# ---------------------------------------------------------------- 消息 ↔ 行


def _text(content: Any) -> str:
    """消息 content（str / 内容块列表）压成纯文本"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return "" if content is None else str(content)


def _role(message: BaseMessage) -> str | None:
    if isinstance(message, HumanMessage):
        return "human"
    if isinstance(message, AIMessage):
        return "ai"
    if isinstance(message, ToolMessage):
        return "tool"
    if isinstance(message, SystemMessage):
        return "system"
    return None


def _json_safe(value: Any) -> Any:
    """tool_calls 里基本只有 str/int/list/dict，兜底处理不可序列化的东西"""
    try:
        json.dumps(value)
        return value
    except TypeError:
        return json.loads(json.dumps(value, default=str))


def message_to_row(
    thread_id: str, user_id: int | None, seq: int, message: BaseMessage
) -> dict[str, Any] | None:
    """把一条消息变成存储行

    **落库前统一掩掉 PII**（FR-2.3 的第二处）：会话历史写在 Milvus 里是长期存储，
    工具返回的原文（知识库条款里常有联系电话）会跟着一起存进去，事后再清就来不及了。
    掩码只影响「存下来的内容」，不影响本轮发给模型的消息。
    """
    role = _role(message)
    if role is None:                       # 不认识的消息类型不入库
        return None

    content = _text(getattr(message, "content", ""))
    if len(content) > MAX_CONTENT_CHARS:
        content = content[:MAX_CONTENT_CHARS] + TRUNCATED_NOTE
    content = mask_pii(content)

    payload: dict[str, Any] = {}
    message_id = getattr(message, "id", None)
    if message_id:
        payload["id"] = str(message_id)

    if role == "ai":
        tool_calls = list(getattr(message, "tool_calls", None) or [])
        if tool_calls:
            payload["tool_calls"] = _json_safe(tool_calls)
    elif role == "tool":
        payload["tool_call_id"] = str(getattr(message, "tool_call_id", "") or "")
        payload["name"] = str(getattr(message, "name", "") or "")
        payload["status"] = str(getattr(message, "status", "success") or "success")
        pending = (getattr(message, "additional_kwargs", None) or {}).get("pending_action")
        if pending:
            payload["pending_action"] = _json_safe(pending)

    return {
        "pk": f"{thread_id}:{int(seq):08d}",
        "thread_id": thread_id,
        "user_id": int(user_id or 0),
        "seq": int(seq),
        "role": role,
        "content": content,
        "payload": mask_payload(payload),      # pending_action.args 里也可能带电话
        "created_at": int(datetime.now().timestamp() * 1000),
        "embedded": False,
        "embedding": [0.0] * VECTOR_DIM,
    }


def row_to_message(row: dict[str, Any]) -> BaseMessage | None:
    role = row.get("role")
    content = row.get("content") or ""
    payload = row.get("payload") or {}
    if isinstance(payload, str):           # 兜底：JSON 字段被读成字符串
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}

    if role == "human":
        return HumanMessage(content=content, id=payload.get("id") or row.get("pk"))
    if role == "ai":
        return AIMessage(
            content=content,
            tool_calls=payload.get("tool_calls") or [],
            id=payload.get("id") or row.get("pk"),
        )
    if role == "tool":
        additional: dict[str, Any] = {}
        if payload.get("pending_action"):
            additional["pending_action"] = payload["pending_action"]
        if payload.get("outcome"):
            additional["outcome"] = payload["outcome"]
        return ToolMessage(
            content=content,
            tool_call_id=payload.get("tool_call_id") or "",
            name=payload.get("name") or None,
            status=payload.get("status") or "success",
            additional_kwargs=additional,
        )
    if role == "system":
        return SystemMessage(content=content)
    return None


# ---------------------------------------------------------------- 窗口化


def window_messages(
    messages: Sequence[BaseMessage],
    *,
    max_turns: int | None = None,
    max_messages: int | None = None,
) -> list[BaseMessage]:
    """只保留最近 N 轮（以用户发言为轮界）+ 总条数上限

    **必须按「轮」切，不能按条切**：AI 的 tool_calls 消息和它的 ToolMessage 是一对，
    从中间截断会留下一条孤立的 ToolMessage，模型直接报「tool_call_id 无对应调用」。
    以 HumanMessage 为界切，天然把一对包在同一轮里。
    """
    if not messages:
        return []

    max_turns = WINDOW_TURNS if max_turns is None else max_turns
    max_messages = WINDOW_MESSAGES if max_messages is None else max_messages

    starts = [index for index, message in enumerate(messages) if isinstance(message, HumanMessage)]
    kept = list(messages[starts[-max_turns]:] if len(starts) > max_turns else messages)

    if len(kept) > max_messages:
        cut = len(kept) - max_messages
        boundary = next(
            (index for index in range(cut, len(kept)) if isinstance(kept[index], HumanMessage)),
            None,
        )
        # 找不到轮界（整段没有用户发言）就硬截尾，宁可少一轮也不能留孤立的 ToolMessage
        kept = kept[boundary:] if boundary is not None else kept[-max_messages:]
    return kept


# ---------------------------------------------------------------- 读


def load_history(
    thread_id: str,
    *,
    user_id: int | None = None,
    max_turns: int | None = None,
    max_messages: int | None = None,
) -> list[BaseMessage]:
    """读回一个会话的最近若干轮（按 seq 升序）

    :param user_id: 传了就校验归属（防越权读别人的会话）；匿名场景可不传
    """
    if not MEMORY_ENABLED or not thread_id:
        return []
    if user_id is not None:
        try:
            ensure_thread_owner(thread_id, user_id)
        except PermissionError as exc:
            logger.warning("拒绝读取会话记忆：{}", exc)
            return []

    rows = get_store().query_thread(thread_id)
    if not rows:
        return []
    messages = [message for message in (row_to_message(row) for row in rows) if message is not None]
    return window_messages(messages, max_turns=max_turns, max_messages=max_messages)


def load_all(thread_id: str, *, user_id: int | None = None) -> list[BaseMessage]:
    """不做窗口的全量历史（确认写操作时用：要能在整段会话里找到那条 tool_call）"""
    return load_history(
        thread_id, user_id=user_id, max_turns=10_000, max_messages=100_000
    )


def load_records(
    thread_id: str, *, user_id: int | None = None, limit: int | None = None
) -> list[dict[str, Any]]:
    """读**原始消息行**（接口层做历史展示用：带 seq / created_at / payload）

    和 `load_history` 的区别：那个返回 LangChain 消息（给模型用），这个返回存储行
    （给前端用：能拿到时间、是否待确认、工具名这些界面需要的字段）。
    """
    if not MEMORY_ENABLED or not thread_id:
        return []
    if user_id is not None:
        try:
            ensure_thread_owner(thread_id, user_id)
        except PermissionError as exc:
            logger.warning("拒绝读取会话记忆：{}", exc)
            return []

    rows = get_store().query_thread(thread_id) or []
    if limit is not None and limit > 0:
        rows = rows[-int(limit):]
    return rows


def list_threads(user_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    """列出一个学生的会话（最近的在前），给前端「历史会话」用

    一次把所有消息行取回来在内存里聚合：单用户的消息量很小（最多几百条），
    比在 Milvus 里做聚合查询简单可靠。
    """
    if not MEMORY_ENABLED or user_id is None:
        return []

    rows = get_store().query_user(int(user_id), limit=5000)
    summary: dict[str, dict[str, Any]] = {}
    for row in rows:
        thread_id = str(row.get("thread_id") or "")
        if not thread_id:
            continue
        entry = summary.setdefault(
            thread_id,
            {"thread_id": thread_id, "messages": 0, "last_at": 0},
        )
        entry["messages"] += 1
        entry["last_at"] = max(entry["last_at"], int(row.get("created_at") or 0))

    ordered = sorted(summary.values(), key=lambda item: item["last_at"], reverse=True)
    return ordered[: max(1, int(limit))]


# ---------------------------------------------------------------- 写


def append_messages(
    thread_id: str, user_id: int | None, messages: Iterable[BaseMessage]
) -> int:
    """把一轮里新增的消息追加进会话（返回实际写入条数）

    写入用 upsert 而不是 insert：Milvus 不强制主键唯一，insert 重放会并存两份。
    seq 直接由「已有最大 seq + 1」推出来，顺带拿到行数做过长裁剪的判断依据。
    """
    if not MEMORY_ENABLED or not thread_id:
        return 0

    messages = list(messages)
    if not messages:
        return 0

    store = get_store()
    if not store.ensure():
        return 0

    with _append_lock:
        rows = store.query_thread(thread_id)
        if rows is None:                   # 读失败：宁可不写，也不要冒 seq 冲突的险
            return 0

        base = max((int(row.get("seq") or 0) for row in rows), default=0)
        new_rows: list[dict[str, Any]] = []
        for offset, message in enumerate(messages, start=1):
            row = message_to_row(thread_id, user_id, base + offset, message)
            if row is not None:
                new_rows.append(row)

        written = store.upsert(new_rows)
        if written:
            _maybe_trim(thread_id, total=len(rows) + len(new_rows))

    if written:
        _enqueue_backfill([row for row in new_rows if row["role"] == "human" and row["content"]])
    return written


def replace_tool_result(
    thread_id: str,
    tool_call_id: str,
    *,
    content: str,
    name: str | None = None,
    status: str = "success",
    outcome: str | None = None,
) -> bool:
    """把「待确认」的那条 ToolMessage 原地替换成执行结果

    为什么必须替换而不是追加：这条 ToolMessage 的 tool_call_id 对应历史里的一条
    AI tool_calls 消息，同一个 tool_call_id 回两条 ToolMessage 会让下一轮请求
    被模型 API 以「一个调用收到了两个结果」拒绝。

    :param outcome: approved / rejected —— 重复确认时用来还原「上次是同意还是拒绝」
    """
    if not MEMORY_ENABLED or not thread_id:
        return False

    store = get_store()
    rows = store.query_thread(thread_id)
    if not rows:
        return False

    target = None
    for row in rows:
        if row.get("role") != "tool":
            continue
        payload = row.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        if str(payload.get("tool_call_id") or "") == str(tool_call_id):
            # 有 pending_action 的优先（那就是等待确认的那条）
            if payload.get("pending_action"):
                target = (row, payload)
                break
            if target is None:
                target = (row, payload)
    if target is None:
        return False

    row, payload = target
    if len(content) > MAX_CONTENT_CHARS:
        content = content[:MAX_CONTENT_CHARS] + TRUNCATED_NOTE
    payload = {**payload, "status": status}
    payload.pop("pending_action", None)
    if name:
        payload["name"] = name
    if outcome:
        payload["outcome"] = outcome

    # 必须补全所有字段：Milvus 的 upsert 是「整行覆盖」，少一个字段就报
    # Insert missed an field。tool 行本来就没有向量，零向量即可。
    # content 同样要在落库前掩掉 PII（与 message_to_row 保持一致）
    updated = {
        "pk": row["pk"],
        "thread_id": thread_id,
        "user_id": int(row.get("user_id") or 0),
        "seq": int(row.get("seq") or 0),
        "role": "tool",
        "content": mask_pii(content),
        "payload": mask_payload(payload),
        "created_at": int(row.get("created_at") or datetime.now().timestamp() * 1000),
        "embedded": False,
        "embedding": [0.0] * VECTOR_DIM,
    }
    return bool(store.upsert([updated]))


def clear_thread(thread_id: str, *, user_id: int | None = None) -> int:
    """清空一个会话（学生点「新会话」/ 隐私清理时用）

    先等后台向量补写跑完再删：否则补写线程可能在我们删完之后把同一批行
    upsert 回来，会话就「删不干净」。
    """
    if user_id is not None:
        ensure_thread_owner(thread_id, user_id)
    wait_for_backfill(timeout=60.0)
    return get_store().drop_thread(thread_id)


def trim_thread(thread_id: str, *, keep: int = MAX_MESSAGES_PER_THREAD) -> int:
    """只保留最近 keep 条（里程碑式的裁剪，避免一个会话无限增长）

    切口要落在**用户发言之前**：直接按条数切会把「AI tool_calls + ToolMessage」
    切散，剩下的孤立 ToolMessage 会让下一轮请求被模型 API 拒绝。
    """
    rows = get_store().query_thread(thread_id)
    if not rows or len(rows) <= keep:
        return 0

    cut = len(rows) - keep
    while cut < len(rows) and rows[cut].get("role") != "human":
        cut += 1
    if cut >= len(rows):               # 切完一条都不剩（数据异常）：不裁
        return 0
    return get_store().delete_pks(row["pk"] for row in rows[:cut])


def _maybe_trim(thread_id: str, *, total: int) -> None:
    if total <= MAX_MESSAGES_PER_THREAD:
        return
    deleted = trim_thread(thread_id, keep=MAX_MESSAGES_PER_THREAD)
    if deleted:
        logger.info("会话 {} 超过 {} 条，已裁剪最旧的 {} 条", thread_id, MAX_MESSAGES_PER_THREAD, deleted)


# ---------------------------------------------------------------- 语义召回（预留）


def recall(
    thread_id: str, query: str, *, k: int = 5, user_id: int | None = None
) -> list[BaseMessage]:
    """在本会话里做一次语义召回（「那它呢」这类追问可以捞回几轮前的内容）

    目前 `load_history` 走的是确定性窗口，没有自动调用本函数；它是为「长期记忆 /
    追问继承」预留的入口，自检脚本里有覆盖。
    """
    if not MEMORY_ENABLED or not thread_id or not query.strip():
        return []
    if user_id is not None:
        ensure_thread_owner(thread_id, user_id)

    vector = _embed_texts([query])
    if not vector:
        return []
    hits = get_store().search(vector[0], thread_id=thread_id, k=k)
    messages: list[BaseMessage] = []
    for hit in hits:
        message = row_to_message({**hit, "payload": hit.get("payload") or {}})
        if message is not None:
            messages.append(message)
    return messages


# ---------------------------------------------------------------- 异步补向量

_backfill_queue: "queue.Queue[list[dict[str, Any]] | None]" = queue.Queue()
_backfill_started = False
_backfill_lock = threading.Lock()
_embed_warned = False


def _embed_texts(texts: Sequence[str]) -> list[list[float]] | None:
    """调共享 bge-m3；缺依赖 / 加载失败时返回 None（记忆退化为纯文本历史）"""
    global _embed_warned
    try:
        from app.rag.embeddings import get_embeddings

        return get_embeddings().embed_documents(list(texts))
    except Exception as exc:                      # noqa: BLE001 —— 记忆不能拖垮主流程
        if not _embed_warned:
            _embed_warned = True
            logger.warning("记忆向量化不可用（语义召回关闭，历史照常）：{}", exc)
        return None


def _backfill_worker() -> None:
    while True:
        batch = _backfill_queue.get()
        try:
            if batch is None:                     # 退出信号
                return
            vectors = _embed_texts([row["content"] for row in batch])
            if not vectors:
                continue
            updated = [
                {**row, "embedding": vector, "embedded": True}
                for row, vector in zip(batch, vectors)
            ]
            get_store().upsert(updated)
        except Exception:                         # noqa: BLE001
            logger.exception("记忆向量补写失败（不影响对话）")
        finally:
            _backfill_queue.task_done()


def _enqueue_backfill(rows: list[dict[str, Any]]) -> None:
    """后台补向量：写入路径只落文本 + 零向量，第一次加载 bge-m3 不阻塞学生"""
    global _backfill_started
    if not EMBED_MESSAGES or not rows:
        return
    with _backfill_lock:
        if not _backfill_started:
            _backfill_started = True
            threading.Thread(
                target=_backfill_worker, name="memory-embedding", daemon=True
            ).start()
    _backfill_queue.put(rows)


def wait_for_backfill(timeout: float = 120.0) -> bool:
    """等后台向量补写完成（自检 / 优雅停机用）；返回是否全部完成"""
    deadline = time.time() + timeout
    while _backfill_queue.unfinished_tasks and time.time() < deadline:
        time.sleep(0.05)
    return not _backfill_queue.unfinished_tasks


# ---------------------------------------------------------------- 统计


def memory_stats(thread_id: str) -> dict[str, Any]:
    """给运维 / 排查看的一行统计"""
    rows = get_store().query_thread(thread_id) or []
    return {
        "thread_id": thread_id,
        "owner": thread_owner(thread_id),
        "session": thread_session(thread_id),
        "messages": len(rows),
        "embedded": sum(1 for row in rows if row.get("embedded")),
        "turns": sum(1 for row in rows if row.get("role") == "human"),
    }


# ---------------------------------------------------------------- 自检


def _self_check() -> None:
    thread_id = "u990001:selftest"
    logger.info("清空自检会话：{} 条", clear_thread(thread_id))
    messages = [
        HumanMessage(content="你好，我想问国家励志奖学金的条件"),
        AIMessage(content="我先查一下知识库"),
        AIMessage(content="", tool_calls=[
            {"name": "retrieval_knowledge_base", "args": {"query": "国家励志奖学金条件"},
             "id": "call_1", "type": "tool_call"},
        ]),
        ToolMessage(content="[1] 《国家励志奖学金实施办法》第三条…", tool_call_id="call_1",
                    name="retrieval_knowledge_base"),
        AIMessage(content="依据 [1]，需要满足……"),
        # 第二轮：用来验证窗口化会按「轮」而不是按「条」截断
        HumanMessage(content="那助学贷款呢"),
        AIMessage(content="助学贷款的规定在另一份文件里……"),
    ]
    written = append_messages(thread_id, 990001, messages)
    logger.info("写入 {} 条", written)

    loaded = load_history(thread_id)
    logger.info("读回 {} 条：{}", len(loaded), [type(m).__name__ for m in loaded])
    assert len(loaded) == len(messages), "读回条数不一致"
    assert loaded[2].tool_calls and loaded[2].tool_calls[0]["id"] == "call_1"
    assert loaded[3].tool_call_id == "call_1"

    windowed = load_history(thread_id, max_turns=1)
    assert windowed[0].content == "那助学贷款呢", "窗口应该从最近一轮的用户发言开始"
    logger.info("窗口化后 {} 条（max_turns=1），首条：{}", len(windowed), windowed[0].content)

    assert thread_owner(thread_id) == 990001
    try:
        ensure_thread_owner(thread_id, 1)
        raise AssertionError("越权校验没生效")
    except PermissionError:
        logger.info("越权校验通过：别的用户读不到这个会话")

    if wait_for_backfill(timeout=180):
        logger.info("向量补写完成，统计：{}", memory_stats(thread_id))
        hits = recall(thread_id, "奖学金需要什么条件")
        logger.info("语义召回 {} 条：{}", len(hits), [m.content[:20] for m in hits])
    else:
        logger.warning("向量补写超时（不影响功能，只是 recall 不可用）")

    replace_tool_result(thread_id, "call_1", content="已取消（自检）", name="retrieval_knowledge_base")
    logger.info("替换后统计：{}", memory_stats(thread_id))

    logger.info("清空自检会话：{} 条", clear_thread(thread_id))
    logger.info("短期记忆自检通过")


if __name__ == "__main__":
    _self_check()
