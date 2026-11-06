"""app.harness —— Agent 编排层（决策层、预约子图、写操作确认都在这里）

对外只有这几个概念：

    judge_rules / decide        决策层：规则层 ① + 轻量意图模型 ②
    build_agent / chat          组装 agent、跑一轮问答（第 ③ 层由主模型自己决定）
    build_middleware            六层图里的闸门（输入检查/权限/输出检查/调用上限/预算/写操作确认）
    get_main_model / get_intent_model   两个角色的模型工厂
    AgentContext / ChatResult   调用上下文与返回结果（ChatResult 带 trace_id / tokens）
    reservation_graph           预约子图（实验室 / 图书馆自习室，见 app/harness/graph.py）
    confirm_action / PendingAction  写操作的「待确认 → 学生确认 → 服务端执行」
    check_daily / circuit_breaker   预算（MySQL 日配额）与失败处理（超时/熔断）

最小用法：

    from app.harness import chat
    result = chat("国家励志奖学金的申请条件是什么？")
    print(result.answer, result.decision.stage, result.used_rag)

**为什么这里用 PEP 562 懒加载（模块级 __getattr__）**

app/tool 里的预约工具要写 `runtime: ToolRuntime[AgentContext]`，于是
app.tool → app.harness.context；而 app.harness.agent 又要 import app.tool 拿工具列表。
如果本文件在 import 时就 eager 把 agent / middleware 全拉进来，两边就成环
（实际踩到过：`ImportError: cannot import name 'RESERVATION_TOOL_NAMES'
from partially initialized module`）。

改成「第一次访问某个名字时才 import 对应子模块」之后：
    * `from app.harness import chat` 照旧能用；
    * `import app.harness` 变得很轻（不会顺手把 torch / 向量库那一串拉起来）；
    * app.tool ↔ app.harness 之间不再有 import 环。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

# 名字 -> 所在子模块（第一次访问时才加载）
_EXPORTS: dict[str, str] = {
    # 调用上下文（app/harness/context.py，不依赖任何子模块，最轻）
    "AgentContext": "app.harness.context",
    "ContextSchema": "app.harness.context",
    # 组装与问答
    "ChatResult": "app.harness.agent",
    "build_agent": "app.harness.agent",
    "build_context": "app.harness.agent",
    "chat": "app.harness.agent",
    "last_text": "app.harness.agent",
    "resolve_thread_id": "app.harness.agent",
    "use_agent": "app.harness.agent",
    "warmup": "app.harness.agent",
    # 写操作确认闸门
    "ConfirmResult": "app.harness.confirm",
    "PendingAction": "app.harness.confirm",
    "confirm_action": "app.harness.confirm",
    "extract_pending_actions": "app.harness.confirm",
    # 决策层
    "DecisionConfig": "app.harness.decision",
    "DecisionOutcome": "app.harness.decision",
    "IntentClassifier": "app.harness.decision",
    "IntentVerdict": "app.harness.decision",
    "decide": "app.harness.decision",
    "get_classifier": "app.harness.decision",
    "RuleVerdict": "app.harness.decision_rules",
    "judge_rules": "app.harness.decision_rules",
    # 中间件
    "HarnessState": "app.harness.middleware",
    "build_middleware": "app.harness.middleware",
    "middleware": "app.harness.middleware",
    "scan_secrets": "app.harness.middleware",
    # 模型工厂
    "get_intent_model": "app.harness.model",
    "get_main_model": "app.harness.model",
    "get_fallback_model": "app.harness.model",
    "fallback_model_name": "app.harness.model",
    # 预算与失败处理
    "BudgetConfig": "app.harness.budget",
    "check_daily": "app.harness.budget",
    "circuit_breaker": "app.harness.resilience",
    "run_tool_with_timeout": "app.harness.resilience",
    "run_tool_with_retry": "app.harness.resilience",
    "is_transient_error": "app.harness.resilience",
    # 预约子图
    "build_reservation_graph": "app.harness.graph",
    "reservation_graph": "app.harness.graph",
}

# 允许 `app.harness.middleware` 这种「按子模块名取属性」的写法继续可用
_SUBMODULES = frozenset(
    {
        "agent",
        "budget",
        "confirm",
        "context",
        "decision",
        "decision_rules",
        "graph",
        "middleware",
        "model",
        "prompts",
        "resilience",
    }
)

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    """PEP 562：第一次访问某个名字时才 import 对应子模块（避免 import 环）"""
    if name in _SUBMODULES:
        return import_module(f"{__name__}.{name}")

    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(import_module(module_path), name)
    globals()[name] = value  # 缓存，后续访问不再走 __getattr__
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__, *_SUBMODULES})
