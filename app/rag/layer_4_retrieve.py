"""第 4 层：检索层

对齐 data/RAG 项目六层总图.md 的第 4 层：
    4.1 查询理解 —— 留空（见下）
    4.2 召回     —— 子块向量召回
    4.3 过滤     —— 相对阈值截断（kb 过滤已决定不做）
    4.4 排序     —— 交叉编码器重排，聚合到「父块」粒度
    4.5 组装     —— 产出结构化 RetrievedContext（拼 prompt 是第 5 层的事）

三个关键设计（都是讨论定下来的）：

1. 重排**子块**、再按 parent_id 做 max-pooling 聚合到父块，顺序不能反。
   先取 top-k 子块再按父块去重会塌成一两个父块 —— 实测「学生申诉的期限是多久」
   的 top5 子块全在第六章，去重后只剩 1 个父块。

2. 聚合时**章内也要聚焦**：以该父块自己的最高分为基准裁剪命中条款。
   基准不能用全局最高分，否则低分父块内部会被裁光、标注变空。

3. 截断两层：相对阈值（相对最高分）+ 字符预算。实测重排分是 sigmoid 的 0~1，
   分离度极高（相关 0.82~0.98、无关 0.00~0.07），相对阈值非常好用。

4.1 为什么留空：
    按档位表它是「进阶档」。多轮指代消解（「那它呢」）在 agent 场景里通常由
    agent 自己改写查询覆盖掉，等出现真实 bad case 再加。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import loguru
from langchain_core.documents import Document
from langchain_core.stores import BaseStore

from app.rag.reranker import SupportsScore

logger = loguru.logger.bind(name=__name__)

# md 的行首 # 只是标记不是内容，喂给重排前抹掉；但标题本身（「### 第六条」→
# 「第六条」）要保留 —— 条号对「第X条怎么规定」这类问题是强判别特征，
# 也正是纯向量检索最容易失手的地方。
_HEADING_MARK = re.compile(r"^#{1,6}\s*", re.MULTILINE)

# 把标题里的连续空白压成一个空格（源文件里有「第六章  学生申诉」这种双空格）
_WHITESPACE = re.compile(r"\s+")

# 每个父块最多带几条命中条款/命中原文给第 5 层。
# 取 4 是实测结果：规章里一章常见 7~10 条，过阈值的通常 4 条左右，
# 断崖就在第 4、5 条之间；给 3 会砍掉一条相关条款。
MAX_HIT_CHILDREN = 4


def _rerank_text(text: str) -> str:
    """喂给重排器的文本：去掉行首 # 标记"""
    return _HEADING_MARK.sub("", text).strip()


@dataclass
class ParentRank:
    """聚合后的一个父块候选（**未截断**，评估脚本用）"""

    parent_id: str
    score: float                    # 父块分 = 命中子块的最高分（max-pooling）
    recall_score: float             # 那个最高分子块的向量召回分，仅用于排查
    hit_articles: list[str] = field(default_factory=list)   # 章内聚焦后，按分数降序
    hit_children: list[str] = field(default_factory=list)   # 命中子块正文（已去 # 标记）


@dataclass
class RetrievedContext:
    """一条检索结果 = 一个父块（通常是一整章）+ 它为什么被选中"""

    parent_id: str
    content: str                    # 父块正文（整章）；怎么拼进 prompt 由第 5 层决定
    score: float                    # 排序依据：重排分；reranker=None 时是向量分
    recall_score: float
    doc_title: str
    chapter: str
    source: str
    kb: str
    hit_articles: list[str] = field(default_factory=list)
    hit_children: list[str] = field(default_factory=list)

    @property
    def citation(self) -> str:
        """引用抬头，第 5 层直接可用

        例：『《普通高等学校学生管理规定》第六章 学生申诉 · 直接相关：第六十四条、第六十条』
        """
        head = f"《{self.doc_title}》" if self.doc_title else ""
        if self.chapter:
            head += _WHITESPACE.sub(" ", self.chapter).strip()
        if self.hit_articles:
            head += " · 直接相关：" + "、".join(self.hit_articles)
        return head


def rank_parents(
    query: str,
    *,
    vectorstore,
    reranker: SupportsScore | None = None,
    k_recall: int = 20,
    hit_score_ratio: float = 0.5,
) -> list[ParentRank]:
    """4.2 召回 → 4.4 重排（子块粒度）→ 聚合到父块，返回**未截断**的父块排序

    评估脚本靠它算「父块召回率@k」和 MRR —— 这两个指标必须看未截断的排序。
    retrieval() 只是在它的结果上做阈值和预算截断。

    :param reranker: None 表示不重排，直接用向量分排序（评估集做 A/B 的开关）
    :param k_recall: 召回多少个子块（实测 20 个约 0.5s）
    :param hit_score_ratio: 章内聚焦阈值，相对该父块自己的最高分
    """
    if not query.strip():
        return []

    # 用 with_score：降级路径要拿向量分排序，排查时也要看它。
    # 注意度量方向：Milvus 的 COSINE 是「越大越相似」；换成 L2 的话排序要反过来
    hits = vectorstore.similarity_search_with_score(query, k=k_recall)
    if not hits:
        logger.info("召回为空：{}", query)
        return []
    children = [doc for doc, _ in hits]
    recall_scores = [float(score) for _, score in hits]

    # ---------------------------------------------------------- 4.4 重排
    if reranker is not None:
        scores = reranker.score(query, [_rerank_text(c.page_content) for c in children])
    else:
        scores = recall_scores          # 降级：直接拿向量分排序

    # ---------------------------------------------------------- 聚合到父块
    # 必须在任何截断**之前**聚合，否则「top-k 子块」会全落在同一个父块里
    grouped: dict[str, dict] = {}
    for child, score, recall in zip(children, scores, recall_scores):
        parent_id = child.metadata.get("parent_id")
        if not parent_id:
            continue
        bucket = grouped.setdefault(
            parent_id, {"best": None, "recall": 0.0, "children": []}
        )
        bucket["children"].append((score, recall, child))
        if bucket["best"] is None or score > bucket["best"]:
            bucket["best"] = score
            bucket["recall"] = recall

    ranked: list[ParentRank] = []
    for parent_id, bucket in sorted(
        grouped.items(), key=lambda kv: kv[1]["best"], reverse=True
    ):
        children_sorted = sorted(bucket["children"], key=lambda item: item[0], reverse=True)

        # 章内聚焦：基准是该父块自己的最高分。第 0 条永远保留，保证标注不为空
        focus_cut = children_sorted[0][0] * hit_score_ratio
        focus = [
            item for index, item in enumerate(children_sorted)
            if index == 0 or item[0] >= focus_cut
        ][:MAX_HIT_CHILDREN]

        articles: list[str] = []
        seen_articles: set[str] = set()
        hit_children: list[str] = []
        for _score, _recall, child in focus:
            article = child.metadata.get("article") or ""
            if article and article not in seen_articles:
                seen_articles.add(article)
                articles.append(article)
            hit_children.append(_rerank_text(child.page_content))

        ranked.append(
            ParentRank(
                parent_id=parent_id,
                score=bucket["best"],
                recall_score=bucket["recall"],
                hit_articles=articles,
                hit_children=hit_children,
            )
        )
    return ranked


def retrieval(
    query: str,
    *,
    vectorstore,
    docstore: BaseStore[str, Document],
    reranker: SupportsScore | None = None,
    k_recall: int = 20,
    k_final: int = 5,
    min_score_ratio: float = 0.3,
    min_score: float = 0.0,
    hit_score_ratio: float = 0.5,
    max_chars: int = 5000,
) -> list[RetrievedContext]:
    """检索：召回子块 → 重排 → 聚合到父块 → 截断 → 取父块内容

    :param reranker: None 表示不重排，直接用向量分排序（评估集 A/B 开关）。
        注意此时 score 是向量相似度，量纲和重排分不同、分布更平缓，
        min_score_ratio 需要另行标定；公平对比时建议传 min_score_ratio=0.0，
        让两条路径都只靠 k_final / max_chars 截断。
    :param k_final: 最多返回几个父块
    :param min_score_ratio: 相对阈值：低于「最高分 × 该比例」的父块丢掉
    :param min_score: 绝对阈值。**故意留 0，等评估集校准**（见注释）
    :param max_chars: 返回父块正文的总字符预算兜底
    """
    ranked = rank_parents(
        query,
        vectorstore=vectorstore,
        reranker=reranker,
        k_recall=k_recall,
        hit_score_ratio=hit_score_ratio,
    )
    if not ranked:
        return []

    # ---------------------------------------------------------- 4.3 截断
    top_score = ranked[0].score
    # 绝对阈值默认 0 = 不拦截，这里刻意留空：
    # 相关度全为 0 时相对阈值会失效（0 × 0.3 = 0），只有绝对阈值能拦住，
    # 而它必须在评估集上校准（拿「该拒答」和「该回答」两侧的分数分布来定），
    # 拍脑袋设一个数会把本来该回答的问题拒掉。等评估集跑完在这里补。
    threshold = max(top_score * min_score_ratio, min_score)
    if top_score <= 0:
        logger.warning(
            "最高分是 {}，语料里可能根本没有相关内容（绝对阈值尚未校准）：{}",
            top_score, query,
        )

    candidates = [item for item in ranked if item.score >= threshold][:k_final]

    # ---------------------------------------------------------- 4.5 取父块
    parent_ids = [item.parent_id for item in candidates]
    parents = docstore.mget(parent_ids)

    missing = [pid for pid, doc in zip(parent_ids, parents) if doc is None]
    if missing:
        # 子块在向量库、父块在 docstore，两边一旦不一致就会静默少给内容，
        # 这里必须留痕（verify_store 抓的就是这类问题）
        logger.warning("父块缺失（子块有、docstore 没有）：{}", missing)

    results: list[RetrievedContext] = []
    used_chars = 0
    for rank, parent in zip(candidates, parents):
        if parent is None:
            continue
        # 字符预算兜底：第一条永远保留，避免直接返回空
        if results and used_chars + len(parent.page_content) > max_chars:
            logger.debug("字符预算已满（{}），丢弃 {}", used_chars, rank.parent_id)
            break                       # 不为了塞进更短的块而打乱相关性顺序
        used_chars += len(parent.page_content)

        meta = parent.metadata
        results.append(
            RetrievedContext(
                parent_id=rank.parent_id,
                content=parent.page_content,
                score=rank.score,
                recall_score=rank.recall_score,
                doc_title=meta.get("doc_title", ""),
                chapter=meta.get("chapter", ""),
                source=meta.get("source", ""),
                kb=meta.get("kb", ""),
                hit_articles=rank.hit_articles,
                hit_children=rank.hit_children,
            )
        )

    logger.info(
        "检索「{}」：召回 {} 子块 → {} 父块 → 返回 {} 个（{} 字）",
        query, k_recall, len(ranked), len(results),
        sum(len(r.content) for r in results),
    )
    return results