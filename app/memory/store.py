"""短期记忆的存储层：**一行 = 一条消息**，落在 Milvus 的一个 collection 里

**为什么把会话放 Milvus**

六层图里「记忆层」的短期记忆 = 会话历史。项目里已经有 Milvus（知识库向量），
再引入 Redis/新表反而多一个要运维的东西；用 Milvus 的好处是：

    * 消息天然带向量 —— 以后要做「跨轮语义召回」（用户说「那它呢」时把
      几轮前问过的那条捞回来）不用再改存储；
    * 过滤条件强（thread_id / user_id / seq 都是标量字段，Milvus 支持 expr）；
    * 已经在跑了，不新增依赖（pymilvus 已在 requirements 里）。

**已知边界（刻意接受）**

    * Milvus 的 `query` 不支持 `ORDER BY`，所以「最近 N 条」是**全量按 thread
      取回后在 Python 里按 seq 排序**再截断。一个会话几百条消息的量级完全没问题，
      真正需要「只取尾部」时再换 MySQL/Redis（届时接口不用变）。
    * Milvus 不强制主键唯一（insert 两次同 pk 会并存两份），所以本模块**只用
      upsert**，绝不用 insert。
    * **可见性靠 Strong 一致性，不靠 flush**：Milvus 默认的 Bounded 一致性下，
      刚 upsert 的行要等 flush（实测 1.5s 起）才能被 `query` 看到；给每次
      `query` / `search` 显式带 `consistency_level="Strong"` 之后立刻可见、
      单次读写约 10ms。写路径因此**不调 flush**。

**失败处理：fail-open**

记忆是增强项，不是必需项：Milvus 挂掉时 `append`/`load` 返回空、日志告警一次，
对话照常进行（只是变成单轮）。这条策略和决策层的 fail-open 一致。

环境变量（都有默认值，不配也能跑）：

    MILVUS_URI                 默认 http://localhost:19530（与 RAG 用的同一个）
    MILVUS_TOKEN               可选，托管 Milvus 用
    MEMORY_COLLECTION          默认 agent_short_term_memory
    MEMORY_VECTOR_DIM          默认 1024（bge-m3）
"""

from __future__ import annotations

import os
import threading
from typing import Any, Iterable

import loguru

logger = loguru.logger.bind(name=__name__)

__all__ = ["MilvusMemoryStore", "COLLECTION_NAME", "VECTOR_DIM", "get_store"]

DEFAULT_URI = os.getenv("MILVUS_URI", "http://localhost:19530")
TOKEN = os.getenv("MILVUS_TOKEN") or None
COLLECTION_NAME = os.getenv("MEMORY_COLLECTION", "agent_short_term_memory")
VECTOR_DIM = int(os.getenv("MEMORY_VECTOR_DIM", "1024"))
CONNECT_TIMEOUT = float(os.getenv("MEMORY_CONNECT_TIMEOUT", "5") or 5)

# Milvus 单次 query 的硬上限；一个会话的消息数远小于它
QUERY_LIMIT = 16384


class MilvusMemoryStore:
    """消息表：pk / thread_id / user_id / seq / role / content / payload / timestamps

    `payload` 是 JSON 字段，用来放结构化的东西（tool_calls、tool_call_id、
    待确认动作），这样不必为每种消息类型加列，也不会被 VARCHAR 长度限制住。
    """

    def __init__(
        self,
        *,
        uri: str | None = None,
        token: str | None = None,
        collection: str | None = None,
        dim: int | None = None,
    ) -> None:
        self.uri = uri or DEFAULT_URI
        self.token = token if token is not None else TOKEN
        self.collection = collection or COLLECTION_NAME
        self.dim = int(dim or VECTOR_DIM)

        self._client: Any | None = None
        self._lock = threading.Lock()
        self._broken = False
        self._warned = False

    # ------------------------------------------------------------------ 连接
    def _disable(self, message: str, *args: Any) -> None:
        """连接级故障：整个记忆层降级为单轮（每进程只告警一次）"""
        self._broken = True
        self._warn_once(message, *args)

    def _warn_once(self, message: str, *args: Any) -> None:
        if not self._warned:
            self._warned = True
            logger.warning(message + "（记忆层已降级为单轮对话，不影响问答）", *args)

    def _op_failed(self, message: str, *args: Any) -> None:
        """单次读写失败：只记日志，**不**把整个记忆层判死（下次还能继续用）"""
        logger.warning(message + "（本次记忆读写跳过，不影响问答）", *args)

    def _build_client(self) -> Any:
        from pymilvus import MilvusClient

        # Strong 一致性：否则刚 upsert 的消息在 query 里看不见（Milvus 的 flush
        # 是异步的，默认 Bounded 会读到稍旧的快照）——「上一句刚说、下一轮读不到」
        # 就是这个坑。用 Strong 之后连 flush 都不用调，单次读写 ~10ms（见 README 说明）。
        kwargs: dict[str, Any] = {
            "uri": self.uri,
            "timeout": CONNECT_TIMEOUT,
            "consistency_level": "Strong",
        }
        if self.token:
            kwargs["token"] = self.token
        client = MilvusClient(**kwargs)
        if not client.has_collection(self.collection):
            self._create_collection(client)
        return client

    def _create_collection(self, client: Any) -> None:
        """建表 + 索引（向量 AUTOINDEX，thread_id 走 INVERTED 倒排）"""
        from pymilvus import DataType

        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("pk", DataType.VARCHAR, is_primary=True, max_length=256)
        schema.add_field("thread_id", DataType.VARCHAR, max_length=256)
        schema.add_field("user_id", DataType.INT64)
        schema.add_field("seq", DataType.INT64)
        schema.add_field("role", DataType.VARCHAR, max_length=32)
        schema.add_field("content", DataType.VARCHAR, max_length=65535)
        schema.add_field("payload", DataType.JSON)
        schema.add_field("created_at", DataType.INT64)
        schema.add_field("embedded", DataType.BOOL)
        schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=self.dim)

        index_params = client.prepare_index_params()
        index_params.add_index(
            field_name="embedding", index_type="AUTOINDEX", metric_type="COSINE"
        )
        index_params.add_index(field_name="thread_id", index_type="INVERTED")

        try:
            client.create_collection(
                self.collection, schema=schema, index_params=index_params
            )
            logger.info("已创建记忆表 {}/{}", self.uri, self.collection)
        except Exception as exc:                      # 并发建表：另一个进程先建好了
            if not client.has_collection(self.collection):
                raise
            logger.debug("记忆表已存在（并发创建）：{}", exc)

    @property
    def client(self) -> Any | None:
        """惰性连接；连不上就返回 None（调用方据此降级）"""
        if self._broken:
            return None
        with self._lock:
            if self._client is not None:
                return self._client
            try:
                self._client = self._build_client()
            except Exception as exc:                  # 连不上 / 没装 pymilvus
                self._disable("记忆层连接 Milvus 失败：{}", exc)
                return None
            return self._client

    def reset(self) -> None:
        """清掉失败状态，下次调用重新连接（长跑服务里想自愈时用）"""
        with self._lock:
            self._client = None
            self._broken = False
            self._warned = False

    def ensure(self) -> bool:
        return self.client is not None

    # ------------------------------------------------------------------ 读
    def query_thread(self, thread_id: str) -> list[dict[str, Any]] | None:
        """取一个会话的全部消息行（按 seq 升序）；失败返回 None（区分「空会话」）"""
        client = self.client
        if client is None:
            return None
        try:
            rows = list(
                client.query(
                    self.collection,
                    filter=f'thread_id == "{_escape(thread_id)}"',
                    output_fields=[
                        "pk", "thread_id", "user_id", "seq", "role",
                        "content", "payload", "created_at", "embedded",
                    ],
                    limit=QUERY_LIMIT,
                    consistency_level="Strong",
                )
            )
        except Exception as exc:
            self._op_failed("读取记忆失败：{}", exc)
            return None
        return sorted(rows, key=lambda row: int(row.get("seq") or 0))

    def count(self, thread_id: str) -> int:
        client = self.client
        if client is None:
            return 0
        try:
            rows = list(
                client.query(
                    self.collection,
                    filter=f'thread_id == "{_escape(thread_id)}"',
                    output_fields=["count(*)"],
                    consistency_level="Strong",
                )
            )
        except Exception as exc:
            self._op_failed("统计记忆条数失败：{}", exc)
            return 0
        if not rows:
            return 0
        return int(next((value for key, value in rows[0].items() if key.startswith("count")), 0))

    def query_user(self, user_id: int, *, limit: int = 200) -> list[dict[str, Any]]:
        """某个学生所有会话的消息行（列会话列表 / 做用户级清理时用）"""
        client = self.client
        if client is None:
            return []
        try:
            return list(
                client.query(
                    self.collection,
                    filter=f"user_id == {int(user_id)}",
                    output_fields=["thread_id", "seq", "created_at"],
                    limit=limit,
                    consistency_level="Strong",
                )
            )
        except Exception as exc:
            self._op_failed("按用户查询记忆失败：{}", exc)
            return []

    def search(
        self,
        vector: list[float],
        *,
        thread_id: str | None = None,
        k: int = 5,
    ) -> list[dict[str, Any]]:
        """向量召回（只召回 embedded=true 的行；零向量行不会被返回）"""
        client = self.client
        if client is None:
            return []
        filters = ["embedded == true"]
        if thread_id:
            filters.append(f'thread_id == "{_escape(thread_id)}"')
        try:
            hits = client.search(
                self.collection,
                data=[list(vector)],
                anns_field="embedding",
                filter=" and ".join(filters),
                limit=int(k),
                output_fields=["thread_id", "seq", "role", "content", "payload"],
                consistency_level="Strong",
            )
        except Exception as exc:
            self._op_failed("记忆向量召回失败：{}", exc)
            return []

        results: list[dict[str, Any]] = []
        for hit in (hits[0] if hits else []):
            row = dict(hit.get("entity") or {})
            row["distance"] = hit.get("distance")
            results.append(row)
        return results

    # ------------------------------------------------------------------ 写
    def upsert(self, rows: Iterable[dict[str, Any]]) -> int:
        """按 pk 覆盖写（幂等：重放同一批行不会产生重复消息）"""
        rows = list(rows)
        if not rows:
            return 0
        client = self.client
        if client is None:
            return 0
        try:
            with self._lock:
                result = client.upsert(self.collection, rows)
        except Exception as exc:
            self._op_failed("写入记忆失败：{}", exc)
            return 0
        return int(result.get("upsert_count") or len(rows))

    def delete_pks(self, pks: Iterable[str]) -> int:
        pks = list(pks)
        if not pks:
            return 0
        client = self.client
        if client is None:
            return 0
        try:
            with self._lock:
                result = client.delete(self.collection, ids=pks)
        except Exception as exc:
            self._op_failed("删除记忆失败：{}", exc)
            return 0
        return int(result.get("delete_count") or 0)

    def drop_thread(self, thread_id: str) -> int:
        rows = self.query_thread(thread_id) or []
        return self.delete_pks(row["pk"] for row in rows)


def _escape(value: str) -> str:
    """Milvus expr 里的字符串字面量：双引号必须转义，否则会拼坏过滤条件"""
    return str(value).replace('"', '\\"')


_default_store: MilvusMemoryStore | None = None
_store_lock = threading.Lock()


def get_store() -> MilvusMemoryStore:
    """进程内默认实例（连不上会降级，不会抛）"""
    global _default_store
    with _store_lock:
        if _default_store is None:
            _default_store = MilvusMemoryStore()
        return _default_store
