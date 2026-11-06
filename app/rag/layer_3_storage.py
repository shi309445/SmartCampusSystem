# ------------------------------------------------------------------ 第 3 层：存储层
"""
3.1 向量化 / 3.2 索引 / 3.3 存储 / 3.4 关联 / 3.5 维护

  子块 → Milvus（一个 collection，所有知识库共用，kb 只当元数据、不做过滤）
  父块 → MySQL
  更新 → 推倒重建：删掉 collection + 整体替换 docstore，不做增量
"""




import loguru

from itertools import islice
from langchain_core.documents import Document

from app.rag.docstore import MySqlDocStore
from app.rag.layer_1_data import get_data
from app.rag.layer_2_treated import data_treating

logger = loguru.logger.bind(name=__name__)

COLLECTION_NAME = "rag_child_chunks"

# 子块元数据契约：字段名 -> (类型, 最大长度)
# 这份表必须和 _build_chunk() 产出的键逐字对应，_assert_chunk_contract() 会校验。
# 长度对中文留了很大余量（Milvus 的 max_length 可能按 UTF-8 字节计）
METADATA_FIELDS: dict[str, tuple[str, int | None]] = {
    "source": ("VARCHAR", 2048),
    "kb": ("VARCHAR", 128),
    "file_name": ("VARCHAR", 512),
    "doc_title": ("VARCHAR", 512),
    "chapter": ("VARCHAR", 512),
    "article": ("VARCHAR", 128),
    "doc_id": ("VARCHAR", 1024),
    "parent_id": ("VARCHAR", 1024),
    "chunk_level": ("VARCHAR", 16),
    "chunk_index": ("INT64", None),
}

# HNSW + COSINE：BGE-M3 输出已归一化，COSINE 是它的标准配法
INDEX_PARAMS = {"index_type": "HNSW", "metric_type": "COSINE",
                "params": {"M": 16, "efConstruction": 200}}
SEARCH_PARAMS = {"metric_type": "COSINE", "params": {"ef": 64}}

DEFAULT_MILVUS_URI = "http://localhost:19530"

# 3.1 分批嵌入：一次性嵌入上万个块会吃爆显存，而且过程要能看见进度
EMBED_BATCH = 256


def get_embeddings(model_name: str = "BAAI/bge-m3", device: str = "cpu"):
    """3.1 向量化：本地 BGE-M3（1024 维）

    函数内 import：让第 1、2 层在没装 torch / Milvus 的环境里也能单独跑。
    BGE-M3 是无指令模型，查询侧不用加前缀；换回 bge-large-zh 就要加。
    """
    from langchain_huggingface import HuggingFaceEmbeddings

    return HuggingFaceEmbeddings(
        model_name=model_name,
        model_kwargs={"device": device},
        encode_kwargs={"normalize_embeddings": True},   # 归一化后 COSINE 与 IP 等价
    )


def _metadata_schema() -> dict:
    """把 METADATA_FIELDS 转成 langchain-milvus 要的 metadata_schema

    为什么必须显式给：不给的话它会拿「第一部文档」的 metadata 去猜字段和类型，
    一旦某个文件的键不一样，建表就会失败或者字段缺失。
    """
    from pymilvus import DataType

    schema = {}
    for name, (dtype_name, max_length) in METADATA_FIELDS.items():
        kwargs = {"max_length": max_length} if max_length else {}
        schema[name] = {"dtype": getattr(DataType, dtype_name), "kwargs": kwargs}
    return schema


def get_child_store(embeddings, *, uri: str = DEFAULT_MILVUS_URI,
                    token: str | None = None, drop_old: bool = False):
    """3.2/3.3 子块向量库：连接 + 建索引

    :param drop_old: 只在「推倒重建」的写路径上传 True —— 它会在**构造时**
        直接删掉整个 collection。查询侧绝对不能传 True，否则查一次就把库删了。
    """
    from langchain_milvus import Milvus

    connection_args: dict[str, str] = {"uri": uri}
    if token:
        connection_args["token"] = token

    return Milvus(
        embedding_function=embeddings,
        collection_name=COLLECTION_NAME,
        connection_args=connection_args,
        metadata_schema=_metadata_schema(),
        enable_dynamic_field=False,   # 必须 False，metadata_schema 才生效
        auto_id=False,                # 用我们的 doc_id 当主键
        drop_old=drop_old,
        index_params=INDEX_PARAMS,
        search_params=SEARCH_PARAMS,
    )


def _assert_chunk_contract(chunks: list[Document]) -> None:
    """入库前自检：键集合与 METADATA_FIELDS 完全一致，值不超长

    Milvus 的报错很含糊（schema 不匹配、值超长都可能报成别的问题），
    在 Python 侧先拦住能省掉大量排查时间。
    """
    if not chunks:
        raise ValueError("没有可入库的块")

    expected = set(METADATA_FIELDS)
    for chunk in chunks:
        got = set(chunk.metadata)
        if got != expected:
            raise ValueError(
                f"元数据键不匹配（doc_id={chunk.metadata.get('doc_id')}）："
                f"缺 {sorted(expected - got)}，多 {sorted(got - expected)}"
            )

    for name, (_, max_length) in METADATA_FIELDS.items():
        if not max_length:
            continue
        longest = max(len(str(chunk.metadata[name])) for chunk in chunks)
        if longest > max_length:
            raise ValueError(f"字段 {name} 最长 {longest} 字，超过 max_length={max_length}")


def data_save(parents: list[Document], children: list[Document], *,
              vectorstore, docstore, batch_size: int = EMBED_BATCH) -> None:
    """3.1/3.3 入库：子块进 Milvus，父块进 docstore

    注意 Milvus **不强制主键唯一**：同一个 pk 插两次会并存两份（langchain 的
    add_documents 走的是 insert，不是 upsert）。所以写之前 collection 必须是
    新建或已清空的 —— 由 get_child_store(drop_old=True) 保证。
    """
    _assert_chunk_contract(children)
    _assert_chunk_contract(parents)

    total = len(children)
    for start in range(0, total, batch_size):
        batch = children[start:start + batch_size]
        vectorstore.add_documents(batch, ids=[c.metadata["doc_id"] for c in batch])
        logger.info("向量化入库 {}/{}", min(start + batch_size, total), total)

    docstore.replace_all((p.metadata["doc_id"], p) for p in parents)
    logger.info("父块入库 {} 个", len(parents))


def rebuild_all(*, embeddings, session_factory, uri: str = DEFAULT_MILVUS_URI,
                token: str | None = None, batch_size: int = EMBED_BATCH):
    """推倒重建：采集 → 切分 → 校验 → 删旧库 → 写入

    顺序是刻意的：读取 / 解析 / 切分 / 校验全部成功之后才动旧数据，
    这一段出问题旧库原样可用。真正的风险窗口在嵌入阶段（见下面的说明）。
    """
    docs = get_data()
    parents, children = data_treating(docs)
    _assert_chunk_contract(children)      # 先校验再删库，别删完才发现数据不对
    _assert_chunk_contract(parents)

    vectorstore = get_child_store(embeddings, uri=uri, token=token, drop_old=True)
    docstore = MySqlDocStore(session_factory)
    data_save(parents, children, vectorstore=vectorstore, docstore=docstore,
              batch_size=batch_size)
    return vectorstore, docstore


def build_retriever(vectorstore, docstore, *, k: int = 6):
    """3.4 关联：子块检索 → 按 parent_id 回查父块

    这只是第 3→4 层的桥：查询理解、过滤、重排、拼装都属于第 4 层。
    注意 Milvus 的过滤参数叫 expr（不是 filter），以后要按 kb 过滤时：
        search_kwargs={"k": k, "expr": 'kb == "management_rules"'}
    """
    from langchain_classic.retrievers import MultiVectorRetriever

    return MultiVectorRetriever(
        vectorstore=vectorstore,
        docstore=docstore,
        id_key="parent_id",
        search_kwargs={"k": k},
    )


def _count_children(vectorstore) -> int:
    client = vectorstore.client
    try:
        rows = client.query(COLLECTION_NAME, filter="", output_fields=["count(*)"])
        return int(rows[0]["count(*)"])
    except Exception:                     # pymilvus 各版本 API 略有差异
        return int(client.get_collection_stats(COLLECTION_NAME).get("row_count", 0))


def _parents_present(vectorstore, parent_ids: list[str]) -> set[str]:
    """这些父块在 Milvus 里有没有子块"""
    quoted = ", ".join(f'"{pid}"' for pid in parent_ids)
    rows = vectorstore.client.query(
        COLLECTION_NAME, filter=f"parent_id in [{quoted}]", output_fields=["parent_id"]
    )
    return {row["parent_id"] for row in rows}


def verify_store(vectorstore, docstore, *, sample_query: str | None = None,
                 sample_size: int = 5) -> str:
    """3.5 维护自检：子块和父块分在两个库，任一边写漏都不会报错

    所以每次重建后跑一下这个，比出事以后再查便宜得多。
    """
    lines = [
        f"子块（Milvus/{COLLECTION_NAME}）：{_count_children(vectorstore)}",
        f"父块（MySQL docstore）      ：{docstore.count()}",
    ]

    parent_ids = list(islice(docstore.yield_keys(), sample_size))
    if parent_ids:
        missing = [p for p in parent_ids if p not in _parents_present(vectorstore, parent_ids)]
        lines.append(
            f"抽样 {len(parent_ids)} 个父块，{len(missing)} 个在 Milvus 里没有子块"
            + (f" → {missing}" if missing else "")
        )

    if sample_query:
        hits = build_retriever(vectorstore, docstore).invoke(sample_query)
        lines.append(f"试查「{sample_query}」→ 命中 {len(hits)} 个父块")
        for hit in hits[:2]:
            where = " ".join(
                x for x in (hit.metadata.get("doc_title"), hit.metadata.get("chapter"),
                            hit.metadata.get("article")) if x
            )
            lines.append(f"    {where} | {hit.page_content[:40]!r}")

    report = "\n".join(lines)
    logger.info("存储自检：\n{}", report)
    return report