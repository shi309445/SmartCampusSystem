"""父块存储 —— 第 3 层 3.3 存储

父块（一颗 = 一个「章」）不进向量库，只按 doc_id 存起来；检索时用子块带回来的
parent_id 回查。langchain 的 MultiVectorRetriever 要求它是个
BaseStore[str, Document]，所以这里按这个接口实现一版 MySQL 落地。

子块和父块分在两个库，任一边写漏都不会报错，只表现为「检索返回空」或「少了内容」，
所以要配 verify_store() 做对账（见 groundwork.py）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator, Sequence

from langchain_core.documents import Document
from langchain_core.stores import BaseStore
from sqlalchemy import (delete, func, insert, select)
from sqlalchemy.orm import Session, sessionmaker

from app.rag.table import RagDocStore

logger = logging.getLogger(__name__)




# 单条 SQL 携带的行数
UPSERT_BATCH = 500




def _batched(items: Sequence, size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


class MySqlDocStore(BaseStore[str, Document]):
    """父块存储：正好实现 BaseStore 要求的那四个方法

    - mget 必须保持入参顺序、并给缺失的 key 返回 None，检索器依赖这个行为
    - 写入用「先删后插」而不是 MySQL 的 ON DUPLICATE KEY：不绑定方言，
      也能用 SQLite 跑测试。语义一样，只是慢一点，我们的量级无所谓
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    # ------------------------------------------------------------ 读
    def mget(self, keys: Sequence[str]) -> list[Document | None]:
        keys = list(keys)
        if not keys:
            return []
        with self._session_factory() as session:
            # RagDocStore 是 ORM 类：用 scalars 拿对象再读属性。
            # 注意 select(ORM类).mappings() 的键是实体名，不是列名
            rows = {
                obj.doc_key: obj
                for obj in session.scalars(
                    select(RagDocStore).where(RagDocStore.doc_key.in_(keys))
                )
            }
        result: list[Document | None] = []
        for key in keys:                        # 保持入参顺序，缺失给 None
            obj = rows.get(key)
            result.append(
                None if obj is None else Document(
                    page_content=obj.content,
                    metadata=json.loads(obj.doc_metadata),
                )
            )
        return result

    def yield_keys(self, *, prefix: str | None = None) -> Iterator[str]:
        with self._session_factory() as session:
            stmt = select(RagDocStore.doc_key)
            if prefix:
                stmt = stmt.where(RagDocStore.doc_key.startswith(prefix))
            yield from session.scalars(stmt)

    # ------------------------------------------------------------ 写
    def mset(self, key_value_pairs: Iterable[tuple[str, Document]]) -> None:
        rows = self._to_rows(key_value_pairs)
        if not rows:
            return
        with self._session_factory() as session:
            self._upsert(session, rows)
            session.commit()

    def mdelete(self, keys: Iterable[str]) -> None:
        keys = list(keys)
        if not keys:
            return
        with self._session_factory() as session:
            for batch in _batched(keys, UPSERT_BATCH):
                session.execute(
                    delete(RagDocStore).where(RagDocStore.doc_key.in_(batch))
                )
            session.commit()

    # ------------------------------------------------------------ 维护（3.5）
    def replace_all(self, key_value_pairs: Iterable[tuple[str, Document]]) -> None:
        """推倒重建：清空 + 全量写入，放在同一个事务里

        单事务是为了避免「清空了但没写完」的半库状态：失败整体回滚，旧数据还在。
        """
        rows = self._to_rows(key_value_pairs)
        with self._session_factory() as session:
            session.execute(delete(RagDocStore))
            if rows:
                self._upsert(session, rows)
            session.commit()
        logger.info("父块库已整体替换：{} 条", len(rows))

    def count(self) -> int:
        with self._session_factory() as session:
            return session.scalar(select(func.count()).select_from(RagDocStore)) or 0

    # ------------------------------------------------------------ 内部
    @staticmethod
    def _to_rows(key_value_pairs: Iterable[tuple[str, Document]]) -> list[dict]:
        return [
            {
                "doc_key": key,
                "content": doc.page_content,
                "doc_metadata": json.dumps(doc.metadata, ensure_ascii=False),
            }
            for key, doc in key_value_pairs
        ]

    @staticmethod
    def _upsert(session: Session, rows: list[dict]) -> None:
        for batch in _batched([row["doc_key"] for row in rows], UPSERT_BATCH):
            session.execute(delete(RagDocStore).where(RagDocStore.doc_key.in_(batch)))
        for batch in _batched(rows, UPSERT_BATCH):
            session.execute(insert(RagDocStore), batch)