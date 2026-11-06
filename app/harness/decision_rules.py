"""决策层第 1 道闸：规则 / 启发式命中判定（不花一分钱、不碰模型）

**它在整条链路里的位置**

    用户提问
      ↓
    ① 规则/启发式闸门（本文件）      —— 命中程度 → 命中 / 不确定 / 未命中
      ↓ 未命中
    ② 轻量模型意图识别（decision.py） —— 简单的“要不要查知识库”二分类
      ↓ 两道都通过
    ③ 大模型自己决定（RAG 当工具，模型自主 tool call）

**为什么规则层不看检索分（一个容易踩的坑）**

最直觉的做法是“先跑一次检索，看 top1 重排分够不够高再决定要不要检索”，但这里不能这么做：

1. 评估集实测（data/eval_retrieval.jsonl，见 app/rag/eval_retrieval.py 的输出）
   正例 top1 最低 0.2044、负例 top1 最高 0.4255 —— **两组分数是重叠的**，
   没有任何单一阈值能把它们分开。所以“检索分”本身就判不准。
2. 预跑检索 = 每次提问都先付一次全量向量召回 + 重排（k_recall=50），
   等于把「按需检索」降级成「固定流程」，违背 RAG 当工具的初衷。

所以规则层只看**问题本身的字面特征**：领域词表（来自 docs/management_rules 的 15 份文件）
+ 问句意图模式 + 离题（闲聊/数学/代码）模式。它便宜、可解释、可离线单测，
代价是会有假阳性 —— 而假阳性正好由第 ③ 层兜住（检索不到就按工具描述如实拒答）。

**三态而不是两态**

    命中 hit     —— 有**专有领域词**（奖学金/处分/申诉/请假…）且分数够，直接放行，
                    **跳过** 第 2 层的轻量模型调用（省钱省延迟）
    不确定 unsure —— 模糊区（只命中“学校/学生/材料/要求”这类泛词），交给第 2 层
    未命中 miss  —— 明显离题（打招呼、算术、写代码），交给第 2 层；若开了
                    `skip_intent_on_offtopic` 则直接否决，省一次调用

两条刻意的取舍（都是被实测数据逼出来的）：

1. **泛词不单独构成“命中”**。“学校有什么要求吗”“辅导员说要交材料”满篇泛词，
   凑分很容易过线，但这类问题到底要不要查校规恰恰是模型才判断得准的。
   所以命中档要求至少有一个专有领域词，其余一律下沉到第 2 层。
2. **规则层“宁可漏判，不可硬判”**：拿不准的一律给 unsure 下沉到第 2 层，
   而不是硬凑成一个命中或未命中。代价是多一次轻量调用（实测约 2 秒），
   换来的是不漏掉真问题 —— 漏判一条该查校规的提问，学生会拿到一个凭记忆编的答案。

“命中程度”用 `hit_ratio = score / STRONG_SCORE` 归一到 0~1 便于打日志和调阈值，
但判定用的是原始分（可解释：一个专有领域词 = 3 分，泛词/问句模式 = 1 分）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Sequence

# ------------------------------------------------------------------ 命中档位

RuleAction = Literal["hit", "unsure", "miss"]

# 原始分阈值（单位见下面的权重表）
HIT_SCORE = 2.0          # ≥ 它：命中
MISS_SCORE = 1.0         # < 它：未命中（再被离题判定加固）
STRONG_SCORE = 4.0       # ≥ 它：强命中（只影响日志与是否加提示，不改变“交给大模型判断”）

# 权重：专有领域词最值钱，泛词和问句模式只做加权
W_SPECIFIC = 3.0
W_GENERIC = 1.0
W_INTENT = 1.0

# ------------------------------------------------------------------ 词表
# 词表直接对着 docs/management_rules/ 下的 15 份文件写。
# 语料扩充时这里必须同步（和 app/tool/rag_tool.py 里的工具描述一起改）。

SPECIFIC_TERMS: tuple[str, ...] = (
    # 奖助学金 / 困难认定 / 助学贷款
    "奖学金", "励志奖学金", "国家奖学金", "助学金", "助学贷款", "生源地贷款",
    "勤工助学", "困难补助", "临时困难补助", "家庭经济困难", "困难认定", "困难生",
    "绿色通道", "学费减免", "缓缴", "资助",
    # 学籍 / 考试 / 纪律
    "学籍", "休学", "复学", "退学", "转专业", "试读", "学业预警", "毕业证", "学位证",
    "结业", "肄业", "考试违规", "作弊", "违纪", "处分", "警告处分", "严重警告",
    "记过", "留校察看", "开除学籍", "补考", "缓考", "重修", "挂科", "旷课", "学分",
    # 申诉 / 权益
    "申诉", "复议", "听证", "投诉", "举报",
    # 请销假 / 宿舍 / 评选 / 事故
    "请销假", "销假", "请假条", "请假", "事假", "病假", "宿舍", "文明宿舍", "查寝",
    "晚归", "夜不归宿", "优秀毕业生", "综合素质测评", "评优", "评选办法",
    "伤害事故", "意外伤害", "学生管理规定", "高等教育法", "教育考试违规处理办法",
    # 费用 / 保障
    "学费", "住宿费", "勤工", "助学",
)

GENERIC_TERMS: tuple[str, ...] = (
    "规定", "办法", "制度", "条例", "细则", "政策", "文件", "章程", "条款",
    "流程", "手续", "材料", "条件", "资格", "标准", "要求", "期限", "额度",
    "申请", "办理", "审批", "认定", "评审", "公示", "名额", "金额", "比例",
    "学院", "学校", "学生", "辅导员", "班主任", "教务处", "学工部", "研究生院",
    "本科", "研究生", "大一", "大二", "大四",
)

# 问句意图：出现这些词说明用户在“问规定”，不是在闲聊
INTENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(怎么|如何|怎样|咋)(办|做|申请|办理|申诉|投诉|查|算)"),
    re.compile(r"(什么|啥)(时候|条件|标准|要求|材料|流程|手续)"),
    re.compile(r"(哪些|多少|多久|几天|几个月|能不能|可不可以|可以吗|是否|有没有|算不算)"),
    re.compile(r"(会不会|会被|要承担|后果|影响毕业|影响学位)"),
    re.compile(r"[?？]"),
)

# 条款号引用：指着具体条款问（“第64条怎么规定的”）。它本身就是**专有信号** ——
# 语料里的条款号只在校内规章里出现，所以它单独就能撑起“命中”。
ARTICLE_REF = re.compile(r"第[一二三四五六七八九十百零〇两0-9]+条")

# 离题信号：命中且没有领域词时，判定为明显离题
GREETING_ONLY = re.compile(
    r"^\s*(你好|您好|hi|hello|hey|嗨|哈喽|在吗|在不在|早上好|中午好|下午好|晚上好|早安|晚安)"
    r"[\s!！。.~～、,，]*$",
    re.IGNORECASE,
)
OFFTOPIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(讲|说|来)(个|一个)?(笑话|段子|故事)"),
    re.compile(r"(唱|来)(首|一首)?歌"),
    re.compile(r"(你|您)(是谁|叫什么|是什么模型|会用|能做什么|是哪个模型)"),
    re.compile(r"^(谢谢|多谢|感谢|好的|好|嗯|收到|明白了|再见|拜拜|没事了|没事|算了)[\s!！。.~～]*$"),
    re.compile(r"(天气|气温|股票|彩票|球赛|游戏|电影|电视剧|小说|星座|运势)"),
    re.compile(r"(写|来|帮我写|生成).{0,6}(代码|函数|脚本|程序|正则|sql|SQL|作文|诗|文案|邮件)"),
    re.compile(r"(python|java|javascript|golang|c\+\+|sql|html|css|bug|报错|异常|编译)", re.IGNORECASE),
    re.compile(r"(翻译成|translate)", re.IGNORECASE),
    re.compile(r"^\s*[\d\s+\-*/×÷().=%^]+\s*[=?？]?\s*$"),        # 纯算式
    re.compile(r"(算一下|计算一下|解个方程|求导|积分|微积分|排列组合)"),
    re.compile(r"(吃什么|去哪玩|推荐个|讲点|聊聊|无聊|心情|喜欢你|女朋友|男朋友)"),
)

# 追问指代：多轮里“那它呢 / 还有吗 / 为什么”这类短句，本身没有领域词，
# 但指代的是上一轮的规章问题，应该继承上一轮的“命中”结论
FOLLOWUP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(那|这|它|他们|他|她|上述|上面|前面|刚刚|刚才)"),
    re.compile(r"(还有呢|还有吗|然后呢|接着呢|具体呢|详细呢|为什么|怎么说|举个例子)"),
    re.compile(r"^(还有|继续|再说说|展开讲讲|详细说说|具体说说)"),
)
FOLLOWUP_MAX_CHARS = 20      # 超过这个长度就不当“追问短句”了


@dataclass
class RuleVerdict:
    """规则层的判定结果（可解释：命中了什么词、为什么这么判）"""

    action: RuleAction
    score: float
    hit_ratio: float                                   # 归一化的“命中程度” 0~1
    strong: bool
    matched_specific: list[str] = field(default_factory=list)
    matched_generic: list[str] = field(default_factory=list)
    matched_intent: list[str] = field(default_factory=list)
    matched_offtopic: list[str] = field(default_factory=list)
    followup: bool = False
    reason: str = ""

    @property
    def decision_text(self) -> str:
        return {"hit": "命中", "unsure": "不确定", "miss": "未命中"}[self.action]


def _match_terms(text: str, terms: Sequence[str]) -> list[str]:
    """按词表找命中词，并去掉被更长命中词包含的短词

    两层意思：
    1. 同一个词出现多次只算一次（“奖学金”出现三次也只加一次分）；
    2. **重叠不重复计分**：“考试违规”命中时不再计“考试”，否则长词天然比短词值钱，
       分数会虚高，阈值就没法解释了。
    """
    matched = [term for term in terms if term in text]
    kept: list[str] = []
    for term in sorted(matched, key=len, reverse=True):
        if not any(term in longer for longer in kept):
            kept.append(term)
    return kept


def _match_patterns(text: str, patterns: Sequence[re.Pattern[str]]) -> list[str]:
    return [pattern.pattern for pattern in patterns if pattern.search(text)]


def looks_like_followup(query: str, previous_turns: Sequence[str] | None) -> bool:
    """短追问 + 上一轮是规章类问题 → 继承命中结论

    `previous_turns` 由调用方传入（最近的用户发言，新的在前）。
    没有历史时直接 False —— 单轮对话不受影响。
    """
    if not previous_turns:
        return False
    if len(query.strip()) > FOLLOWUP_MAX_CHARS:
        return False
    if not _match_patterns(query, FOLLOWUP_PATTERNS):
        return False
    # 上一轮至少要有领域信号，才值得继承
    return any(judge_rules(turn).action != "miss" for turn in previous_turns[:2])


def judge_rules(query: str, previous_turns: Sequence[str] | None = None) -> RuleVerdict:
    """规则 / 启发式命中判定（纯函数，可离线单测）

    :param query: 用户这一轮的原始问题（口语原文即可）
    :param previous_turns: 最近的用户发言（新的在前），多轮追问继承用；单轮传 None
    """
    text = (query or "").strip()
    if not text:
        return RuleVerdict(
            action="miss", score=0.0, hit_ratio=0.0, strong=False,
            reason="空输入",
        )

    specific = _match_terms(text, SPECIFIC_TERMS)
    generic = _match_terms(text, GENERIC_TERMS)
    intent = _match_patterns(text, INTENT_PATTERNS)
    offtopic = _match_patterns(text, OFFTOPIC_PATTERNS)
    if GREETING_ONLY.match(text):
        offtopic.append("greeting_only")

    # 条款号引用算一个专有信号（“第64条怎么规定的”没有任何领域名词，但显然是问校规）
    article_ref = bool(ARTICLE_REF.search(text))
    if article_ref:
        specific = ["第N条", *specific]

    # 多轮追问：继承上一轮的命中，但分数打折，避免“那它呢”被当成强命中
    followup = looks_like_followup(text, previous_turns)

    score = (
        W_SPECIFIC * len(specific)
        + W_GENERIC * len(generic)
        + W_INTENT * len(intent)
    )
    if followup and not specific:
        score += W_SPECIFIC
    score = round(score, 2)

    # 离题否决：明显离题且没有任何专有信号 → 直接未命中。
    # 注意这里**不看泛词**（“学校/学生”在闲聊里也常出现），否则“学校食堂几点开门”
    # 会靠泛词混进命中档。
    if offtopic and not specific and not followup:
        return RuleVerdict(
            action="miss", score=score, hit_ratio=min(1.0, score / STRONG_SCORE),
            strong=False,
            matched_specific=specific, matched_generic=generic,
            matched_intent=intent, matched_offtopic=offtopic,
            reason=f"离题信号：{offtopic[:3]}",
        )

    action: RuleAction
    if followup:
        action = "hit"
        reason = "多轮追问，继承上一轮的规章主题"
    elif not specific:
        # 关键约束：**没有专有领域词就不算命中**。
        # “学校有什么要求吗”“辅导员说要交材料”这类句子全是泛词，
        # 靠泛词凑到 HIT_SCORE 只会让规则层变成万金油；它们应该落到模糊区，
        # 由第 2 层的轻量模型（或最终的主模型）去判断 —— 这正是分两层的意义。
        action = "miss" if score < MISS_SCORE else "unsure"
        reason = (
            f"只有泛词/问句信号，没有专有领域词（{score} 分）→ 交给第 2 层"
            if action == "unsure"
            else f"没有任何领域/问句信号（{score} 分 < {MISS_SCORE}）"
        )
    elif score >= HIT_SCORE:
        action = "hit"
        reason = f"命中 {specific}（{score} 分 ≥ {HIT_SCORE}）"
    else:
        # 有专有领域词但分不够（当前权重下几乎不会发生，留着兜底）
        action = "unsure"
        reason = f"命中 {specific} 但分不够（{score} 分 < {HIT_SCORE}）"

    return RuleVerdict(
        action=action,
        score=score,
        hit_ratio=round(min(1.0, score / STRONG_SCORE), 3),
        strong=bool(specific) and score >= STRONG_SCORE,
        matched_specific=specific,
        matched_generic=generic,
        matched_intent=intent,
        matched_offtopic=offtopic,
        followup=followup,
        reason=reason,
    )
