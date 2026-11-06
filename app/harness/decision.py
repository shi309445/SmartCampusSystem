"""决策层：决定这一轮要不要把「知识库检索工具」交给大模型

**三道闸门，两道便宜的在前面，贵的在后面**

    用户提问
      │
      ├─ ① 规则 / 启发式（decision_rules.py，0 成本、0 延迟）
      │      命中 → 放行（跳过第 2 层，省一次模型调用）
      │      不确定 / 未命中 ↓
      ├─ ② 轻量模型意图识别（本文件 IntentClassifier）
      │      need_rag=True → 放行
      │      need_rag=False → 否决（工具从列表里摘掉）
      │      ↓ 两道都通过
      └─ ③ 主模型自己决定（RAG 当工具；工具在，模型自主 tool call，也可能不调）

**“通过”的含义**：第 1、2 层都是**闸门**，不是**执行器**。它们只决定
“这一轮的检索工具要不要出现在主模型的工具列表里”，真正“调不调、调几次”
仍然由主模型决定 —— 这正是用户选定的策略（第 1 层强命中只加一条引导提示，
不强制 tool call）。

**为什么要有第 2 层，而不是让规则层直接否决**

规则层只有词表，遇到“我家里出了点事，学费还没交上，学校能帮吗”这种
**一个领域词都没命中、但明确要查资助办法**的提问会漏判。第 2 层用一次
廉价模型调用把这类问题捞回来；反过来，它也能拦住“学校附近哪家奶茶好喝”
这种被泛词（“学校”）误命中的问题。

**失败处理：fail-open**

第 2 层挡在用户和主模型之间，它自己绝不能变成故障点。模型超时、返回不是 JSON、
额度报错 —— 一律**放行**（`allow_rag=True`），把决定权交回主模型（第 3 层本来
就有这个能力）。宁可多给一次检索机会，也不要因为一个轻量模型抽风就拒答。
这个行为由 `HARNESS_INTENT_FAIL_OPEN=0` 可切成 fail-close。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal, Sequence

import loguru
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.harness.decision_rules import RuleVerdict, judge_rules
from app.harness.prompts import (
    HINT_INTENT_CONFIRMED,
    HINT_NO_RAG,
    HINT_STRONG_HIT,
    INTENT_SYSTEM_PROMPT,
    INTENT_USER_TEMPLATE,
)
from app.observability import spans
from app.utils.guard import mask_pii

logger = loguru.logger.bind(name=__name__)

# RAG 工具名（必须和 app/tool/rag_tool.py 里的函数名一致）；
# 中间件靠它在 tool 列表里做“摘掉 / 放行”。
RAG_TOOL_NAME = "retrieval_knowledge_base"

# 决策是怎么做出来的（打日志、排查用）
DecisionStage = Literal[
    "rule_hit",                 # 第 1 层命中
    "intent_confirmed",         # 第 1 层未命中 → 第 2 层判定需要查
    "intent_denied",            # 第 1 层未命中 → 第 2 层判定不需要查
    "offtopic_short_circuit",   # 明显离题，跳过第 2 层直接否决（可关，见配置）
    "context_override",         # 调用方用 context.force_rag 强制放行/否决
    "intent_error_fail_open",   # 第 2 层出错 → 放行
    "intent_error_fail_closed", # 第 2 层出错 → 否决
    "budget_exhausted",         # 今日配额已用尽，连第 2 层都不再调用（省钱）
    "disabled",                 # 决策层整体关闭
]


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _number(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


@dataclass
class DecisionConfig:
    """决策层开关（全部走环境变量，不新增 .env 键也能有默认值）"""

    enabled: bool = field(default_factory=lambda: _flag("HARNESS_DECISION_ENABLED", True))
    enable_intent_model: bool = field(default_factory=lambda: _flag("HARNESS_INTENT_ENABLED", True))
    # 明显离题（招呼/纯算式/代码）时跳过第 2 层，省一次模型调用。
    # 想严格按“第 1 层没命中就必须过第 2 层”跑，把它设成 0。
    skip_intent_on_offtopic: bool = field(
        default_factory=lambda: _flag("HARNESS_SKIP_INTENT_ON_OFFTOPIC", True)
    )
    # 第 2 层出错时是否放行（见模块文档的 fail-open 说明）
    fail_open: bool = field(default_factory=lambda: _flag("HARNESS_INTENT_FAIL_OPEN", True))
    # 意图模型最多等多久（秒）；超时按出错处理
    intent_timeout: float = field(default_factory=lambda: _number("INTENT_TIMEOUT", 8.0))


@dataclass
class IntentVerdict:
    """第 2 层轻量模型的判定结果"""

    need_rag: bool
    intent: str = ""
    confidence: float = 0.0
    reason: str = ""
    model: str = ""
    latency_ms: float = 0.0
    raw: str = ""
    error: str | None = None        # 非空表示这次分类失败了（结果来自 fail-open/close）
    tokens_in: int = 0              # 意图模型的真实用量（有 usage 就用，没有就按字数估）
    tokens_out: int = 0

    @property
    def tokens_total(self) -> int:
        return max(0, int(self.tokens_in)) + max(0, int(self.tokens_out))


@dataclass
class DecisionOutcome:
    """决策层的最终结论：这一轮要不要把检索工具交给主模型"""

    allow_rag: bool
    stage: DecisionStage
    rule: RuleVerdict
    intent: IntentVerdict | None
    hint: str                        # 注入给主模型的一次性提示（"" 表示不加）
    query: str
    intent_tokens: int = 0           # 第 2 层这次模型调用消耗的 token（交给预算层记账）
    latency_ms: float = 0.0

    def __post_init__(self) -> None:
        # 决策层里只有第 2 层会花钱；把用量挂到 outcome 上，middleware 不必再摸
        # classifier 的内部状态，也能在没走意图模型（规则命中）时保持 0。
        if self.intent is not None and not self.intent_tokens:
            self.intent_tokens = self.intent.tokens_total

    @property
    def strong_hit(self) -> bool:
        return self.stage == "rule_hit" and self.rule.strong

    def summary(self) -> dict[str, Any]:
        """一行日志用的精简结构（不打印学生问题全文之外的隐私字段）"""
        return {
            "allow_rag": self.allow_rag,
            "stage": self.stage,
            "rule": f"{self.rule.action}/{self.rule.score}",
            "hit": (self.rule.matched_specific or self.rule.matched_generic)[:3],
            "intent": None if self.intent is None else {
                "need_rag": self.intent.need_rag,
                "intent": self.intent.intent,
                "confidence": self.intent.confidence,
                "error": self.intent.error,
            },
            "latency_ms": round(self.latency_ms, 1),
        }

    def log(self) -> None:
        # 问题原文可能含学生手机号/邮箱；日志只留掩码后的版本（FR-2.3 第三处）
        logger.info(
            "决策层：{} ← {}",
            json.dumps(self.summary(), ensure_ascii=False),
            mask_pii(self.query),
        )


# ------------------------------------------------------------------ 第 2 层：轻量意图识别

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
_CODE_FENCE = re.compile(r"```(?:json)?|```", re.IGNORECASE)


def _content_to_text(content: Any) -> str:
    """模型返回的 content 可能是 str，也可能是内容块列表（含 thinking 块）"""
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
    return str(content)


def parse_intent_payload(text: str) -> dict[str, Any] | None:
    """从模型输出里抠出 JSON（容忍代码块、前后废话、中文标点）"""
    cleaned = _CODE_FENCE.sub("", text or "").strip()
    match = _JSON_BLOCK.search(cleaned)
    if not match:
        return None
    candidate = match.group(0)
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


class IntentClassifier:
    """决策第 2 层：一次廉价的二分类调用

    刻意**不用** structured_output：不同 provider（deepseek / openai 兼容 / 本地模型）
    对 function calling、response_format 的支持参差不齐，而一个 200 token 的 JSON
    用正则兜底反而最稳。解析失败按出错处理（走 fail-open）。
    """

    def __init__(self, model: Any | None = None, *, timeout: float = 8.0) -> None:
        self._model = model
        self.timeout = timeout

    @property
    def model(self):
        if self._model is None:
            # 惰性导入 + 惰性建模型：没装 provider 包时，决策层第 1 层仍然能独立工作
            from app.harness.model import get_intent_model

            self._model = get_intent_model()
        return self._model

    @property
    def model_name(self) -> str:
        from app.harness.model import describe_models

        return describe_models()["intent"]

    def classify(self, query: str) -> IntentVerdict:
        started = time.perf_counter()
        stats = spans.current()
        if stats is not None:
            stats.begin_model()

        messages: list[BaseMessage] = [
            SystemMessage(content=INTENT_SYSTEM_PROMPT),
            HumanMessage(content=INTENT_USER_TEMPLATE.format(query=query)),
        ]
        try:
            response = self.model.invoke(messages, config={"timeout": self.timeout})
        except Exception as exc:                      # 网络 / 额度 / 超时：交给上层 fail-open
            logger.warning("意图识别调用失败：{}", mask_pii(str(exc)))
            if stats is not None:
                # 失败的调用也是一次调用，记一条 model.intent span（tokens 记 0）
                stats.end_model(role="intent")
            return IntentVerdict(
                need_rag=False, model=self.model_name, error=str(exc),
                latency_ms=(time.perf_counter() - started) * 1000,
            )

        text = _content_to_text(getattr(response, "content", response))

        # 意图模型的 token 以前完全没进预算 / 回合汇总；这里统一取 usage，
        # provider 不回 usage 就按输出字数估一个下限（宁可估多，不要漏记）。
        from app.harness.budget import TokenUsage, estimate_tokens, extract_usage

        usage = extract_usage(response)
        if usage.total_tokens <= 0:
            estimated = estimate_tokens(text)
            usage = TokenUsage(input_tokens=0, output_tokens=estimated, total_tokens=estimated)
        if stats is not None:
            stats.end_model(
                role="intent", tokens_in=usage.input_tokens, tokens_out=usage.output_tokens
            )

        payload = parse_intent_payload(text)
        latency = (time.perf_counter() - started) * 1000
        if payload is None or "need_rag" not in payload:
            logger.warning("意图识别返回无法解析：{!r}", mask_pii(text[:200]))
            return IntentVerdict(
                need_rag=False, model=self.model_name, raw=text, error="输出不是合法 JSON",
                latency_ms=latency,
                tokens_in=usage.input_tokens, tokens_out=usage.output_tokens,
            )

        return IntentVerdict(
            need_rag=bool(payload.get("need_rag")),
            intent=str(payload.get("intent", ""))[:16],
            confidence=float(payload.get("confidence") or 0.0),
            reason=str(payload.get("reason", ""))[:60],
            model=self.model_name,
            latency_ms=latency,
            raw=text,
            tokens_in=usage.input_tokens,
            tokens_out=usage.output_tokens,
        )


@lru_cache(maxsize=1)
def get_classifier() -> IntentClassifier:
    """进程内单例（模型对象复用，别每次提问都重新建）"""
    return IntentClassifier(timeout=DecisionConfig().intent_timeout)


# ------------------------------------------------------------------ 决策主流程


def decide(
    query: str,
    *,
    previous_turns: Sequence[str] | None = None,
    config: DecisionConfig | None = None,
    classifier: IntentClassifier | None = None,
) -> DecisionOutcome:
    """跑完 1、2 两道闸门，给出“这轮要不要提供检索工具”

    :param query: 用户这一轮的原始问题
    :param previous_turns: 最近的用户发言（新的在前），多轮追问继承用；单轮传 None
    :param config: 决策层开关
    :param classifier: 注入的意图分类器（测试用；默认走单例）
    """
    config = config or DecisionConfig()
    started = time.perf_counter()
    rule = judge_rules(query, previous_turns)

    # ---------------------------------------------------------- 关闭决策层
    if not config.enabled:
        return DecisionOutcome(
            allow_rag=True, stage="disabled", rule=rule, intent=None,
            hint="", query=query,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    # ---------------------------------------------------------- ① 规则层命中
    if rule.action == "hit":
        hint = ""
        if rule.strong:
            matched = "、".join((rule.matched_specific or rule.matched_generic)[:3])
            hint = HINT_STRONG_HIT.format(matched=matched or "领域词")
        outcome = DecisionOutcome(
            allow_rag=True, stage="rule_hit", rule=rule, intent=None,
            hint=hint, query=query,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        outcome.log()
        return outcome

    # ------------------------------------------- 明显离题：可选跳过第 2 层（省一次调用）
    if config.skip_intent_on_offtopic and rule.matched_offtopic and not rule.matched_specific:
        outcome = DecisionOutcome(
            allow_rag=False, stage="offtopic_short_circuit", rule=rule, intent=None,
            hint=HINT_NO_RAG, query=query,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        outcome.log()
        return outcome

    # ---------------------------------------------------------- ② 轻量意图模型
    if not config.enable_intent_model:
        # 第 2 层被关掉时，规则层没命中就等于否决（此时不引入任何模型成本）
        outcome = DecisionOutcome(
            allow_rag=False, stage="intent_denied", rule=rule, intent=None,
            hint=HINT_NO_RAG, query=query,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        outcome.log()
        return outcome

    verdict = (classifier or get_classifier()).classify(query)

    if verdict.error is not None:
        allow = config.fail_open
        outcome = DecisionOutcome(
            allow_rag=allow,
            stage="intent_error_fail_open" if allow else "intent_error_fail_closed",
            rule=rule, intent=verdict, hint="" if allow else HINT_NO_RAG, query=query,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        outcome.log()
        return outcome

    if verdict.need_rag:
        outcome = DecisionOutcome(
            allow_rag=True, stage="intent_confirmed", rule=rule, intent=verdict,
            hint=HINT_INTENT_CONFIRMED.format(intent=verdict.intent or "可能与校规相关"),
            query=query,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
    else:
        outcome = DecisionOutcome(
            allow_rag=False, stage="intent_denied", rule=rule, intent=verdict,
            hint=HINT_NO_RAG, query=query,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
    outcome.log()
    return outcome
