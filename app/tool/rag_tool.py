"""agent 工具：校园规章制度知识库检索

这是 RAG 给 agent 的**唯一入口**。按「RAG 当工具」的架构，5.1~5.3
（拼上下文 / 提示词 / 调模型）属于 agent 那一侧，所以这个工具只负责：

    1. 调第 4 层检索（召回 → 重排 → 聚合到父块 → 截断）
    2. 过一道闸门：明显离题的直接不返回资料（5.5 的 A 层）
    3. 把结果排成**带编号的资料块** —— 它同时是 agent 的上下文、
       引用编号的来源、以及将来做引用校验的白名单（5.5 的 B 层）
    4. 匹配度不高时明确提示，让 agent 自己判断资料够不够

**防幻觉的三条约束写在工具的文档字符串里**。在「RAG 当工具」的架构下，
工具描述就是 RAG 影响提示词的地方 —— agent 每次决定要不要调、怎么用，
都会看到它。生成之后的引用校验属于 agent 侧中间件，不在这里。
"""

from __future__ import annotations

import loguru
from langchain.tools import tool

from app.database import SessionLocal
from app.rag.docstore import MySqlDocStore
from app.rag.embeddings import get_embeddings
from app.rag.layer_1_data import scan_docs
from app.rag.layer_3_storage import get_child_store
from app.rag.layer_4_retrieve import RetrievedContext, retrieval
from app.rag.reranker import get_reranker
from app.utils.guard import mark_external_content, mask_pii, wrap_external_content

logger = loguru.logger.bind(name=__name__)

# ---------------------------------------------------------------- 检索参数

K_RECALL = 50          # 召回多少子块再重排。评估脚本用 --k-recall 对比：
                       # 100 的时候候选覆盖 100%；50 待测（延迟大约减半）
K_FINAL = 5            # 最多返回几个父块（通常被相对阈值先截到 1~2 个）
MAX_CHARS = 5000       # 返回正文的总字符预算

# ---------------------------------------------------------------- 闸门阈值
# 这两个数来自评估集 data/eval_retrieval.jsonl 的实测：
#   正例 top1 最低 0.2044，负例 top1 最高 0.4255 —— **两组是重叠的**，
#   没有任何单一阈值能分开它们。所以闸门只负责拦「明显离题」的那一档，
#   0.15~0.5 这段模糊区交给模型判断（靠工具描述里的约束 + 下面的提示）。
MIN_SCORE = 0.15       # 低于它：不返回任何资料
LOW_CONFIDENCE = 0.5   # 低于它但高于 MIN_SCORE：返回资料，但提示匹配度不高

LOW_CONFIDENCE_NOTE = (
    "【注意】以下资料与问题的匹配度不高，可能只是内容相近、并不直接回答这个问题。"
    "如果资料不足以回答，请如实告诉学生没有查到明确规定，不要推测。"
)

# 拒答话术。语料扩充时，上面的工具描述和这里的范围说明要同步更新
NO_RESULT_TEMPLATE = (
    "没有查到与学生问题相关的条款。\n"
    "（已检索知识库：{file_count} 份校内文件）\n"
    "请如实告诉学生没有查到相关规定，并说明已经查过知识库；"
    "不要用你自己的知识补充，也不要把「知识库里没有」说成「学校没有这个规定」。"
)

# ---------------------------------------------------------------- 懒加载资源
# 模型加载一次要好几秒，绝对不能每次工具调用都重建

_ready = False
_vectorstore = None
_docstore = None
_reranker = None
_file_count = 0


def _init() -> None:
    """首次调用时把 embedding / 向量库 / 重排模型都准备好"""
    global _ready, _vectorstore, _docstore, _reranker, _file_count
    if _ready:
        return

    # 进程内单例：记忆层（app/memory）写消息向量时也用这一份，避免把 bge-m3 加载两次
    embeddings = get_embeddings()
    _vectorstore = get_child_store(embeddings)   # 读路径：绝不传 drop_old
    _docstore = MySqlDocStore(SessionLocal)
    _reranker = get_reranker()
    try:
        _file_count = len(scan_docs().files)
    except Exception:                            # 只为拒答话术服务，失败不影响检索
        _file_count = 0
    _ready = True


def warmup_knowledge_base() -> None:
    """预热：把模型和连接提前加载好

    agent 启动时调一次（约 6~10 秒），否则第一个学生的问题要干等这么久。
    """
    _init()
    logger.info("知识库检索已预热：{} 份文件", _file_count)


# ---------------------------------------------------------------- 输出组装


def format_context(results: list[RetrievedContext]) -> str:
    """把检索结果排成带编号的资料块（= 可引用白名单）

    编号就是 agent 引用时该用的编号（「依据 [1]」），也是将来做引用校验的判据。
    格式刻意保持可解析：`[序号] 《文件名》章 · 直接相关：第X条`

    **外层包一道"外部内容"边界**（app/utils/guard.py）：
    检索回来的正文是外部数据，不是指令。开头的声明告诉模型"这里面说的话不算数"，
    单条命中注入话术时再加 `【⚠已标记可疑指令】` 前缀（只标记，不删正文）。
    这是防**间接注入**的关键一步 —— 只检查用户输入是防不住的，
    因为攻击面在知识库文档里。
    """
    blocks = [
        f"[{index}] {item.citation}\n{mark_external_content(item.content)}"
        for index, item in enumerate(results, start=1)
    ]
    return wrap_external_content(blocks)


# ---------------------------------------------------------------- 工具本体


@tool
def retrieval_knowledge_base(query: str) -> str:
    """查询校园规章制度知识库，返回与学生问题相关的原始条款。

知识库收录的校内文件：学生管理规定 / 学籍与考试违规处理 / 奖学金 /
助学金与助学贷款 / 家庭经济困难学生认定 / 勤工助学 / 请销假 /
宿舍文明建设 / 优秀毕业生评选 / 新生绿色通道 / 学生伤害事故处理 等。

什么时候用：学生问到学校的规章制度、评选办法、申请条件、办理流程、
纪律处分、申诉投诉等具体规定时，先用这个工具查，不要凭记忆回答。

必须遵守：
1. 只依据返回的资料作答，不要用你自己的知识补充或推测条款内容。
2. 返回「没有查到相关条款」或资料明显不相关时，如实告诉学生没有查到，
   并说明已经查过知识库；绝对不要编造条款号或规定内容。
3. 不要把「知识库里没有」说成「学校没有这个规定」——后者很可能是错的。
4. 「直接相关」里列出的条款优先级最高，优先依据它们回答。

Args:
    把学生的问题原文传进来即可，用口语就行，不需要改写成书面语或条款号。

Returns:
    若干条编号资料，每条包含来源文件、章、直接相关的条款号，以及条款原文。
引用时请使用资料编号，例如「依据 [1]」。


"""
    if not query or not query.strip():
        return "没有收到有效的问题，无法检索。请把学生的问题原文传进来。"

    try:
        _init()
        results = retrieval(
            query,
            vectorstore=_vectorstore,
            docstore=_docstore,
            reranker=_reranker,
            k_recall=K_RECALL,
            k_final=K_FINAL,
            min_score=MIN_SCORE,
            max_chars=MAX_CHARS,
        )
    except Exception:
        # 工具里不要往外抛异常：agent 拿到一条错误信息还能继续对话，
        # 抛出去整个 agent 循环就断了（重试/降级属于中间件的失败处理层）
        logger.exception("知识库检索失败：{}", mask_pii(query))
        return (
            "知识库检索出错，暂时拿不到资料。"
            "请如实告诉学生系统暂时无法查询，建议稍后再试或直接咨询相关职能部门，"
            "不要凭常识作答。"
        )

    if not results:
        logger.info("未命中（min_score={}）：{}", MIN_SCORE, mask_pii(query))
        return NO_RESULT_TEMPLATE.format(file_count=_file_count)

    top_score = results[0].score
    logger.info("命中 {} 条，top1={:.4f}：{}", len(results), top_score, mask_pii(query))

    context = format_context(results)
    if top_score < LOW_CONFIDENCE:
        logger.info("低置信放行（top1={:.4f}）：{}", top_score, mask_pii(query))
        return f"{LOW_CONFIDENCE_NOTE}\n\n{context}"
    return context