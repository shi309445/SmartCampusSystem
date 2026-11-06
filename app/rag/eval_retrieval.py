"""检索层评估：召回率@k / 严格命中率@k / MRR / 候选覆盖 / min_score 校准

用法（在项目根目录下）：
    .\.venv\Scripts\python.exe -m app.rag.eval_retrieval
    .\.venv\Scripts\python.exe -m app.rag.eval_retrieval 路径.jsonl --k-recall 100
    .\.venv\Scripts\python.exe -m app.rag.eval_retrieval > baseline.txt

评估集条目格式（二选一）：
    {"id": 1, "source": "普通高等学校学生管理规定（节选）.md",
     "question": "...", "expected_articles": ["第六十条", "第六十二条"]}
    {"id": 2, "question": "...",
     "expected": [{"source": "XX.md", "article": "第十二条"}]}     # 跨文件时用这个
    {"id": 3, "question": "...", "expected_articles": [], "pending": true}   # 暂不计分

为什么必须带 source：
    「条款号」只在单个文件内唯一。这份语料里 69 个条款号有 46 个重复出现
    （「第一条」出现在 15 个文件里），所以「条款号 → 父块」必须用
    (文件, 条款号) 做键，否则指标会张冠李戴。

四个指标，越往下越严：
    候选覆盖      —— 期望父块有没有进候选集（**向量召回的能力**，reranker 救不了这一层）
    父块召回率@k  —— 期望父块有没有进重排后的前 k
    条款命中率@k  —— 期望(文件,条款)有没有出现在前 k 个父块的 hit_articles 里（宽松，
                     因为一章最多列 MAX_HIT_CHILDREN 条）
    严格命中率@k  —— 期望条款是不是某个父块的**第 1 命中条款**（看排序质量）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import loguru
from langchain_huggingface import HuggingFaceEmbeddings

from app.config import BASE_DIR, settings
from app.rag.layer_1_data import get_data
from app.rag.layer_2_treated import data_treating
from app.rag.layer_3_storage import get_child_store
from app.rag.layer_4_retrieve import rank_parents
from app.rag.reranker import get_reranker

logger = loguru.logger.bind(name=__name__)

K_LIST = (1, 3, 5, 10, 20)


def load_items(path: Path) -> list[dict]:
    items = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno} JSON 解析失败：{exc}") from exc
    return items


def build_article_index() -> tuple[dict[tuple[str, str], str], list[tuple[str, str]]]:
    """(source, article) -> parent_id

    重复检测只报「同一 (文件,条款号) 落在**不同父块**」的情况 —— 那才是真异常。
    同一父块内出现多次是硬切续块（超长条款被切开、各块共享同一个 article 锚点，
    续块正文以【第X条】开头），属于设计内行为，不报。
    """
    _parents, children = data_treating(get_data())
    mapping: dict[tuple[str, str], str] = {}
    parents_of: dict[tuple[str, str], set[str]] = {}
    for child in children:
        article = child.metadata.get("article")
        source = child.metadata.get("source")
        if not article or not source:
            continue
        key = (source, article)
        parents_of.setdefault(key, set()).add(child.metadata["parent_id"])
        mapping.setdefault(key, child.metadata["parent_id"])
    duplicates = [key for key, pids in parents_of.items() if len(pids) > 1]
    return mapping, duplicates


def resolve_source(name: str, known: set[str]) -> str | None:
    """允许评估集只写文件名（不带 kb 目录前缀）"""
    if name in known:
        return name
    hits = [s for s in known if s.split("/")[-1] == name]
    return hits[0] if len(hits) == 1 else None


def item_pairs(item: dict, known_sources: set[str]) -> list[tuple[str, str]]:
    """把条目里的期望解析成 [(source, article), ...]"""
    raw: list[tuple[str, str]] = []
    if "expected" in item:
        raw = [(e.get("source", ""), e["article"]) for e in item["expected"]]
    else:
        raw = [(item.get("source", ""), a) for a in item.get("expected_articles", [])]

    pairs = []
    for source_name, article in raw:
        if not source_name:
            candidates = [s for s in known_sources] if len(known_sources) == 1 else []
            resolved = candidates[0] if len(candidates) == 1 else None
        else:
            resolved = resolve_source(source_name, known_sources)
        if resolved:
            pairs.append((resolved, article))
        else:
            logger.warning("评估集里的文件解析不了：{}", source_name)
    return pairs


def describe(values: list[float]) -> str:
    if not values:
        return "（无）"
    ordered = sorted(values)
    return f"最低 {ordered[0]:.4f} / 中位 {ordered[len(ordered) // 2]:.4f} / 最高 {ordered[-1]:.4f}"


def best_threshold(pos: list[float], neg: list[float]):
    """扫描使 balanced accuracy=(TPR+TNR)/2 最大的阈值

    注意它往往贴着正例最低那条，是**上界**，实际取值要留余量。
    """
    if not pos or not neg:
        return None
    best = None
    for threshold in sorted(set(pos + neg)):
        tp = sum(1 for s in pos if s >= threshold)
        tn = sum(1 for s in neg if s < threshold)
        tpr, tnr = tp / len(pos), tn / len(neg)
        accuracy = (tpr + tnr) / 2
        if best is None or accuracy > best[0]:
            best = (accuracy, threshold, tp, tn)
    return best


def evaluate_item(item, vectorstore, reranker, index, k_recall) -> dict:
    expected_pairs = set(item_pairs(item, {s for s, _ in index}))
    expected_pids = {index[pair] for pair in expected_pairs if pair in index}
    ranked = rank_parents(item["question"], vectorstore=vectorstore,
                          reranker=reranker, k_recall=k_recall)

    row = {
        "item": item,
        "expected_pairs": expected_pairs,
        "top1_score": ranked[0].score if ranked else 0.0,
        "top1_citation": "",
        "rank": None,
        "candidate_cover": False,
        "parent_hit": {}, "article_hit": {}, "strict_hit": {},
    }
    if not ranked:
        for k in K_LIST:
            row["parent_hit"][k] = row["article_hit"][k] = row["strict_hit"][k] = False
        return row

    if ranked[0].hit_articles:
        source = ranked[0].parent_id.rsplit("::", 1)[0].split("/")[-1]
        row["top1_citation"] = f"{source} · {ranked[0].hit_articles[0]}"

    row["candidate_cover"] = any(c.parent_id in expected_pids for c in ranked)

    for position, candidate in enumerate(ranked, start=1):
        if row["rank"] is None and candidate.parent_id in expected_pids:
            row["rank"] = position

    for k in K_LIST:
        top_k = ranked[:k]
        row["parent_hit"][k] = any(c.parent_id in expected_pids for c in top_k)
        # 关键修正：命中要**同时**比对文件名，否则同名条款号会张冠李戴
        loose = {
            (c.parent_id.rsplit("::", 1)[0], article)
            for c in top_k for article in c.hit_articles
        }
        row["article_hit"][k] = bool(loose & expected_pairs)
        strict = {
            (c.parent_id.rsplit("::", 1)[0], c.hit_articles[0])
            for c in top_k if c.hit_articles
        }
        row["strict_hit"][k] = bool(strict & expected_pairs)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="检索层评估")
    parser.add_argument("path", nargs="?", default=str(BASE_DIR / "data" / "eval" / "eval_retrieval.jsonl"))
    parser.add_argument("--k-recall", type=int, default=100,
                        help="召回多少个子块再重排（默认 100；只会增加候选、不会改变已有候选的名次）")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.is_file():
        print(f"找不到评估集：{path}")
        return
    items = load_items(path)
    index, duplicates = build_article_index()
    known_sources = {s for s, _ in index}

    if duplicates:
        print(f"!! 有 {len(duplicates)} 个 (文件, 条款号) 在语料里重复出现（切分或原文有问题）：")
        for source, article in duplicates[:10]:
            print(f"     {source.split('/')[-1]}  {article}")

    unknown = sorted({a for it in items for _, a in item_pairs(it, known_sources)
                      if (_, a) not in index} - {a for _, a in index})
    embedded = [it for it in items if not it.get("pending")]
    print(f"评估集 {len(items)} 条（{len(embedded)} 条计分，{len(items) - len(embedded)} 条 pending）"
          f"｜k_recall={args.k_recall}")

    embeddings = HuggingFaceEmbeddings(
        model_name=str(settings.EMBEDDING_MODEL_PATH),
        model_kwargs={"device": "cpu"},          # 4GB 显存：embedding 留 CPU
        encode_kwargs={"normalize_embeddings": True},
    )
    vectorstore = get_child_store(embeddings)
    reranker = get_reranker()

    rows = [evaluate_item(it, vectorstore, reranker, index, args.k_recall) for it in embedded]
    answerable = [r for r in rows if r["expected_pairs"]]
    refused = [r for r in rows if not r["expected_pairs"]]

    print("\n=== 逐题（宽松/严格 = @1；@5 已全中，只看 @1 才有信息量）===")
    print("   id   top1    覆盖  宽松@1 严格@1  第1命中 / 期望                            问题")
    for row in rows:
        item = row["item"]
        expect = "、".join(f"{s.split('/')[-1][:6]}·{a}" for s, a in row["expected_pairs"]) or "（应拒答）"
        loose = "✓" if row["article_hit"].get(1) else ("—" if not row["expected_pairs"] else "✗")
        strict = "✓" if row["strict_hit"].get(1) else ("—" if not row["expected_pairs"] else "✗")
        cover = "✓" if row["candidate_cover"] else ("—" if not row["expected_pairs"] else "✗")
        print(f"  {item['id']:>3}  {row['top1_score']:.4f}  {cover}    {loose}    {strict}   "
              f"{row['top1_citation'][:22]:<24} {expect[:26]:<28} {item['question']}")

    total = len(answerable)
    print(f"\n=== 汇总（{total} 条有答案 + {len(refused)} 条应拒答）===")
    cover = sum(1 for r in answerable if r["candidate_cover"])
    print(f"  候选覆盖（向量召回）  {cover:>2}/{total} = {cover / total:6.1%}")
    for k in K_LIST:
        p = sum(1 for r in answerable if r["parent_hit"][k])
        a = sum(1 for r in answerable if r["article_hit"][k])
        s = sum(1 for r in answerable if r["strict_hit"][k])
        print(f"  @{k:<2} 父块召回 {p:>2}/{total} = {p / total:6.1%}"
              f"   条款命中 {a:>2}/{total} = {a / total:6.1%}"
              f"   严格命中 {s:>2}/{total} = {s / total:6.1%}")
    mrr = sum(1 / r["rank"] for r in answerable if r["rank"]) / total
    print(f"  MRR = {mrr:.4f}")

    print("\n=== 正例里分数最低的 10 条（阈值会切在这一带）===")
    for row in sorted(answerable, key=lambda r: r["top1_score"])[:10]:
        expect = "、".join(f"{s.split('/')[-1][:8]}·{a}" for s, a in row["expected_pairs"])
        print(f"  {row['top1_score']:.4f}  id={row['item']['id']:<3} "
              f"第1命中={row['top1_citation'][:26]:<28} 期望={expect:<22} {row['item']['question']}")

    print("\n=== 分数分布（min_score 校准依据）===")
    pos = [r["top1_score"] for r in answerable]
    neg = [r["top1_score"] for r in refused]
    print(f"  有答案组 top1：{describe(pos)}")
    print(f"  应拒答组 top1：{describe(neg)}")
    best = best_threshold(pos, neg)
    if best:
        accuracy, threshold, tp, tn = best
        print(f"  扫描出的最优阈值 = {threshold:.4f} → 命中 {tp}/{len(pos)}，拦住 {tn}/{len(neg)}"
              f"（balanced accuracy {accuracy:.3f}）")
        if min(pos) > 0 < max(neg):
            geometric = (min(pos) * max(neg)) ** 0.5
            print(f"  几何中点（建议起步值）= {geometric:.4f}"
                  f"  —— 给正例最低那条留 {min(pos) / geometric:.1f} 倍余量")
        print("  提醒：最优阈值贴着正例最低那条，是**上界**；样本少时别直接写死")

    print("\n=== @1 未中的题（先看这一屏）===")
    misses = [r for r in answerable if not r["article_hit"].get(1)]
    if not misses:
        print("  （@1 也全部命中）")
    for row in misses:
        if not row["candidate_cover"]:
            stage = "向量召回就没覆盖到 → 该加 k_recall 或上混合检索"
        elif row["rank"]:
            stage = f"覆盖到了，但父块排在第 {row['rank']} 位 → 重排问题"
        else:
            stage = "在候选集里但没进排序"
        print(
            f"  id={row['item']['id']:<3} top1={row['top1_score']:.4f} 第1命中={row['top1_citation'][:30]:<32} {row['item']['question']}  —— {stage}")

    print("\n=== 应拒答组里分数最高的几条（最容易被误答）===")
    for row in sorted(refused, key=lambda r: r["top1_score"], reverse=True)[:5]:
        print(f"  {row['top1_score']:.4f}  id={row['item']['id']:<3} {row['item']['question']}")

    if unknown:
        print(f"\n!! 有 {len(unknown)} 个条款号在语料里找不到（可能标注写错或语料没有）：{unknown}")


if __name__ == "__main__":
    main()