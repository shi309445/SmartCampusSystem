"""Harness 主入口：组装 agent、跑一轮问答

**这个文件和旧版的区别**

旧版有三处一跑就挂的问题，一并修掉了：
1. `from app.harness import middleware_test` —— 这个名字从来没定义过（import 即报错）；
2. 用了不存在的变量 `tool_test`（应该是 `app.tool.user_tool`）；
3. 模块顶层直接 `while True: input(...)` —— 只要被 import 就阻塞，没法当模块用。
   现在收进 `if __name__ == "__main__":`。

**一轮问答里谁在做事**

    chat(trace_id)
      └─ RAGAgent.invoke
           ├─ 预算检查点 budget_guard_middleware   ← 日配额 / 单轮 token 上限（超了直接结束）
           ├─ 中间件 input_and_decision_middleware ← 输入检查 + 决策层 ①②（规则/轻量模型）
           ├─ 预算提示 budget_hint_middleware      ← 接近上限时要求收敛
           ├─ 决策闸门 rag_decision_gate           ← 放行/摘掉检索工具，注入一次性提示
           ├─ 主模型（第 ③ 层，自己决定要不要调检索）
           │    └─ retrieval_knowledge_base（app/tool，第 4 层检索 + 闸门 + 编号资料）
           ├─ 工具权限检查 tool_permission_middleware ← 白名单 + 超时 + 熔断
           ├─ 写操作闸门 write_confirmation_middleware ← 预约/取消只「提议」，不执行
           ├─ 输出检查 output_guard_middleware
           └─ token 计量 budget_after_model_middleware ← 累加并写 MySQL 日配额

**记忆（谁负责持久化）**

    thread_id = context.thread_id / context.session_id / （由 user_id 兜底生成）
    chat() 会：进来时从 app/memory 读回最近若干轮 → 跑一轮 → 把本轮新增消息写回。

    没有 user_id 就没有 thread_id，也就没有记忆（匿名单轮），行为和以前一致。
    **写操作不会在这里执行**：它只返回 pending_actions，由调用方（CLI / FastAPI）
    收集学生确认后调 app/harness/confirm.py 的 confirm_action()。

**两种用法**

    # 1) 当模块用（Web 层将来接这个）
    from app.harness.agent import chat
    from app.harness.confirm import confirm_action
    result = chat("帮我约明天下午 2 点的实验室", context=AgentContext(user_id=7, user_name="张三"))
    result.answer, result.decision.allow_rag, result.thread_id, result.pending_actions
    result.trace_id, result.tokens          # 排障 / 计费（trace 建议放进响应头 X-Trace-Id）
    if result.pending_actions:
        confirm_action(tool_call_id=result.pending_actions[0].tool_call_id, user_id=7, approve=True, thread_id=result.thread_id)

    # 2) 命令行单跑（带登录态才能触发记忆 / 预约）
    .\\.venv\\Scripts\\python.exe -m app.harness.agent --user 7
"""

from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Sequence
from uuid import uuid4

import loguru
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.harness.context import AgentContext, ContextSchema  # noqa: F401 —— ContextSchema 为旧名兼容而保留
from app.harness.decision import RAG_TOOL_NAME
from app.harness.middleware import HarnessState, build_middleware
from app.harness.model import (
    ModelConfigError,
    describe_models,
    fallback_model_name,
    get_fallback_model,
    get_main_model,
)
from app.harness.prompts import MAIN_SYSTEM_PROMPT
from app.memory import append_messages, load_history, make_thread_id, thread_owner
from app.observability import spans
from app.tool import user_tool
from app.utils.trace import set_trace_context

logger = loguru.logger.bind(name=__name__)

# 关掉 langchain 内部那层 DEBUG 日志（它会把每次 tool call 的完整参数打出来）
logging.getLogger("langchain").setLevel(logging.WARNING)

# 降级彻底失败时给学生的兜底话术（FR-1.9：宁可说清"这次没答完整"，也不要 500）
FALLBACK_ANSWER = (
    "系统现在有点忙，这次没能把问题处理完。请稍后再问一次；"
    "如果一直这样，联系管理员检查一下模型配置。"
)

# FR-1.10：连续降级才升级为 ERROR 告警（单次降级已经有一条 WARNING）。
# 进程内计数即可：单进程部署；多副本时每个 worker 各自计数，仍然是"连续"语义。
_degraded_streak = 0
_degraded_streak_lock = threading.Lock()


def _note_degraded(fallback_name: str) -> int:
    """记一次降级；连续第 3/6/9… 次时打一条 ERROR，提示检查主模型"""
    global _degraded_streak
    with _degraded_streak_lock:
        _degraded_streak += 1
        streak = _degraded_streak
    if streak >= 3 and streak % 3 == 0:
        logger.error(
            "连续 {} 次降级（最近备用模型 {}），请检查主模型配置 / 网络",
            streak,
            fallback_name,
        )
    return streak


def _reset_degraded_streak() -> None:
    global _degraded_streak
    with _degraded_streak_lock:
        _degraded_streak = 0

# AgentContext / ContextSchema 定义在 app/harness/context.py：
# 工具侧（app/tool/reservation_tool.py）要用 ToolRuntime[AgentContext] 标注，
# 放在这里会和 app.tool 形成循环 import。这里继续 re-export，保持对外 API 不变。


@dataclass
class ChatResult:
    """一轮问答的结果：答案 + 决策过程 + 原始消息（排查用）

    记忆与写操作相关的三项：

    - `thread_id`：这一轮用的会话 id（None = 没开记忆，匿名单轮）
    - `pending_actions`：本轮「等学生确认」的写操作（app/harness/confirm.PendingAction）
    - `turn_messages`：本轮**新增**的消息（不含历史），排查 / 落库用

    可观测性两项：

    - `trace_id`：串起「日志 / LangSmith run / 会话 / 预约记录」的那条线，
      HTTP 层建议放进响应头 `X-Trace-Id`
    - `tokens`：本轮消耗的 token（预算层的计量结果，拿不到就是 0）

    失败处理相关：

    - `degraded`：这一轮是否走了降级（主模型失败 → 备用模型 / 兜底话术）
    - `fallback_model`：降级时实际用的模型名（没降级为 None）
    """

    answer: str
    decision: Any | None = None
    messages: list[BaseMessage] = field(default_factory=list)
    thread_id: str | None = None
    pending_actions: list[Any] = field(default_factory=list)
    turn_messages: list[BaseMessage] = field(default_factory=list)
    trace_id: str | None = None
    tokens: int = 0
    model_calls: int = 0
    daily_tokens: int = 0
    degraded: bool = False
    fallback_model: str | None = None

    @property
    def used_rag(self) -> bool:
        """这一轮模型实际有没有调用检索工具（和 decision.allow_rag 不是一回事）"""
        if not self.decision or not self.decision.allow_rag:
            return False
        for message in self.messages:
            if isinstance(message, AIMessage) and message.tool_calls:
                if any(call.get("name") == RAG_TOOL_NAME for call in message.tool_calls):
                    return True
        return False

    @property
    def needs_confirmation(self) -> bool:
        """本轮是否有写操作在等学生确认（HTTP 层据此返回 need_confirm）"""
        return bool(self.pending_actions)


def build_agent(
    *,
    model: Any | None = None,
    tools: Sequence[Any] | None = None,
    middleware: Sequence[Any] | None = None,
):
    """组装 agent

    agent 本身无状态（状态全在 messages 里），所以既可以在服务启动时建一次复用，
    也可以每轮新建。`chat()` 不传 agent 时就是每轮新建。
    """
    return create_agent(
        model=model or get_main_model(),
        tools=list(tools if tools is not None else user_tool),
        system_prompt=MAIN_SYSTEM_PROMPT,
        middleware=list(middleware if middleware is not None else build_middleware()),
        # 决策结论要挂在 state 上，所以显式声明扩展后的 schema
        state_schema=HarnessState,
        context_schema=AgentContext,
        name="smart_campus_agent",
    )


def build_context(user_input: str, history: Sequence[BaseMessage] | None = None) -> dict[str, Any]:
    """把「历史 + 本轮提问」拼成 agent 的输入

    历史从哪来由调用方决定：`chat()` 在开了记忆（有 thread_id）时会先用
    `app/memory.load_history()` 读出最近若干轮（按轮窗口化，见 short_term.py）；
    调用方也可以直接传 `history` 覆盖。

    本轮用户消息**显式带 id**：输入检查（中间件）命中注入/超长时要用同一个 id
    原地替换这条消息，没有 id 就只能退化成“追加一条标记”。
    """
    messages: list[Any] = list(history or [])
    messages.append(HumanMessage(content=user_input, id=str(uuid4())))
    return {"messages": messages}


def resolve_thread_id(
    context: AgentContext,
    thread_id: str | None = None,
    session_id: str | None = None,
) -> str | None:
    """决定本轮用哪个会话 id；返回 None 表示不开记忆（匿名单轮）

    优先级：显式 thread_id > context.thread_id > （user_id + session_id 拼出来）
    > 由 user_id 兜底成 `u{user_id}`。

    三条硬规则：
    1. **未登录一定没有记忆**（否则任何匿名请求都能往别人的会话里写）；
    2. thread_id 必须能反解出 user_id（格式 `u{user_id}[:session]`），
       反解不出来或对不上当前用户 → 直接拒绝，相当于关闭记忆；
    3. 拒绝只影响记忆，不影响问答（学生照样能问问题）。
    """
    if context.user_id is None:
        if thread_id or getattr(context, "thread_id", None):
            logger.warning("未登录请求带了 thread_id，忽略（匿名单轮）")
        return None

    explicit = thread_id or getattr(context, "thread_id", None)
    if explicit:
        owner = thread_owner(explicit)
        if owner is None:
            logger.warning("thread_id 格式不合法（应为 u{{user_id}}[:session]）：{}", explicit)
            return None
        if owner != int(context.user_id):
            logger.warning("拒绝使用不属于当前用户的 thread_id={}", explicit)
            return None
        return explicit

    return make_thread_id(context.user_id, session_id or getattr(context, "session_id", None))


def _invoke_with_fallback(
    runner: Any,
    payload: dict[str, Any],
    ctx: AgentContext,
    run_config: dict[str, Any],
) -> tuple[dict[str, Any] | None, bool, str | None]:
    """跑一轮 agent；主模型**运行时**失败就换备用模型，都失败返回 (None, True, 名字)

    **为什么可以整轮重跑**

    写操作在中间件里就被拦成「待确认」了（`write_confirmation_middleware`），
    invoke 期间不会真正落库；能真正执行的只有只读查询和检索，重复一次没有副作用。
    所以这里重跑的是"同一份输入 + 同一份历史"，不会造成重复下单。

    **重跑的真实代价（不要假装没有）**

    降级是**重跑整个 agent 图**，不是只换生成模型，所以：
        * `before_agent` 的决策层会再跑一次（命中规则层时 0 成本；否则多一次意图模型调用）；
        * 已经执行过的**只读**工具会再查一次（幂等，毫秒级）；
        * 写操作永远不会被重跑（被闸门拦在图外）。
    连接类失败时 token 没有真正消耗，所以多花的钱只在"决策层那次轻量调用"上。
    想把这段省掉，需要把决策结论通过 AgentContext 带进第二次 invoke —— 收益
    （罕见路径上省一次 200 token 的调用）不值这个复杂度，先记在这里。

    **为什么 ModelConfigError 不降级**

    缺 API Key / 缺 provider 包属于部署配置问题，备用模型也一样起不来。
    让它照常抛，由 `model_config_exception_handler` 返回 503 —— 这才是运维该看到的信号，
    伪装成"系统忙"只会让问题更难查。
    """
    try:
        result = runner.invoke(payload, context=ctx, config=run_config)
        _reset_degraded_streak()                 # 主模型正常：连续降级计数清零
        return result, False, None
    except ModelConfigError:
        raise
    except Exception as exc:                     # noqa: BLE001 —— 降级是最后一道防线
        fallback = get_fallback_model()
        name = fallback_model_name()

        if fallback is None or name is None:
            logger.error(
                "主模型调用失败（{}），且未配置 FALLBACK_MODEL，无法降级：{}",
                type(exc).__name__, exc,
            )
            raise

        logger.warning(
            "主模型调用失败（{}：{}），降级到备用模型 {}", type(exc).__name__, exc, name
        )
        try:
            fallback_agent = build_agent(model=fallback)
            metadata = dict(run_config.get("metadata") or {})
            metadata.update({"degraded": True, "fallback_model": name})
            fallback_config = {**run_config, "metadata": metadata}
            # 标记这一跑是降级：随后模型 span 会记成 model.fallback，而不是 main
            spans.mark_fallback()
            result = fallback_agent.invoke(payload, context=ctx, config=fallback_config)
            _note_degraded(name)
            return result, True, name
        except Exception:                        # noqa: BLE001 —— 备用也挂：给兜底话术
            logger.exception("备用模型 {} 也不可用，本轮降级为兜底话术", name)
            _note_degraded(name)
            return None, True, name


def chat(
    user_input: str,
    *,
    history: Sequence[BaseMessage] | None = None,
    agent: Any | None = None,
    context: AgentContext | None = None,
    config: dict[str, Any] | None = None,
    thread_id: str | None = None,
    session_id: str | None = None,
    trace_id: str | None = None,
) -> ChatResult:
    """跑一轮问答（开了记忆时会自动读历史 + 写回本轮消息）

    :param history: 之前的消息（不传且开了记忆 → 从 app/memory 读最近若干轮）
    :param agent: 复用外部 agent（省掉每轮重建；不传就新建）
    :param context: AgentContext（user_id / user_name / user_role / thread_id / session_id / force_rag）
    :param thread_id: 覆盖 context.thread_id（格式 `u{user_id}` 或 `u{user_id}:{session_id}`）
    :param session_id: 覆盖 context.session_id（一个学生多条会话时用）
    :param trace_id: 复用调用方（HTTP 中间件）已经生成的 trace_id；不传就新建。
       这样「一条 HTTP 请求的日志」和「这一轮 agent 的日志」是同一个 trace。

    写操作（预约 / 取消）不会在这里执行：它们只以 `pending_actions` 的形式返回，
    学生确认后由 `app.harness.confirm.confirm_action()` 落库。
    """
    ctx = context or AgentContext()
    resolved_thread = resolve_thread_id(ctx, thread_id, session_id)

    # trace_id：日志 patcher、LangSmith run metadata、ChatResult 共用同一个
    resolved_trace = set_trace_context(
        trace_id, user_id=ctx.user_id, thread_id=resolved_thread
    )

    if history is None and resolved_thread:
        history = load_history(resolved_thread, user_id=ctx.user_id)

    runner = agent or build_agent()
    payload = build_context(user_input, history)
    input_messages = list(payload["messages"])

    # LangSmith 的 run 上挂业务 id：Dashboard 里能按 trace_id / user_id 搜到这一轮
    run_config = dict(config or {})
    run_config.setdefault("run_name", "chat")
    metadata = dict(run_config.get("metadata") or {})
    metadata.update(
        {
            "trace_id": resolved_trace,
            "user_id": ctx.user_id,
            "thread_id": resolved_thread,
        }
    )
    run_config["metadata"] = metadata

    # 开一轮：把统计对象挂到上下文，供中间件（工具 / 模型 span）就地记账。
    # 必须在 invoke 之前开，否则第一次工具调用的埋点会丢掉。
    stats = spans.start_turn(resolved_trace, ctx.user_id, resolved_thread)

    try:
        result, degraded, fallback_used = _invoke_with_fallback(runner, payload, ctx, run_config)

        if result is None:
            # 主备模型都挂了：伪造一次"已答完"的结果，让后面的记忆 / 结果组装照常走完，
            # 学生拿到一句能看懂的话，而不是 500（FR-1.9）。
            messages = [*input_messages, AIMessage(content=FALLBACK_ANSWER)]
            result = {"messages": messages, "turn_tokens": 0, "turn_model_calls": 0}
        else:
            messages = list(result.get("messages", []))

        # 本轮要写回记忆的消息 = **本轮用户输入 + 之后新增的消息**。
        # 只取「新增」会漏掉用户自己那句（它在 input 里），下一轮就接不上；
        # 把整段 input 都写回又会让历史重复入库。所以取「最后一条输入 + 新增」，
        # 且最后一条输入用 result 里的那份（输入检查命中时它被同 id 原地替换过）。
        if len(messages) >= len(input_messages):
            current_input = messages[len(input_messages) - 1]
            turn_messages = [current_input, *messages[len(input_messages):]]
        else:                                     # 消息被合并/裁剪过：保守地整段写回
            turn_messages = list(messages)

        if resolved_thread and turn_messages:
            written = append_messages(resolved_thread, ctx.user_id, turn_messages)
            if written:
                logger.debug("本轮 {} 条消息写入会话 {}", written, resolved_thread)

        # 延迟导入：confirm 依赖 app.tool，放在模块顶层虽可行但没有必要（且更慢）
        from app.harness.confirm import extract_pending_actions

        pending_actions = extract_pending_actions(messages)
        tokens = int(result.get("turn_tokens") or 0)
        model_calls = int(result.get("turn_model_calls") or 0)

        logger.info(
            "本轮完成｜trace={}｜user={}｜thread={}｜模型调用={}｜tokens={}｜待确认={}"
            "｜决策={}｜降级={}",
            resolved_trace,
            ctx.user_id,
            resolved_thread,
            model_calls,
            tokens,
            len(pending_actions),
            getattr(result.get("rag_decision"), "stage", "-"),
            fallback_used or degraded,
        )

        # 收尾数据给回合汇总用（span 里已经记了工具次数 / 重试 / 模型耗时）
        stats.degraded = degraded
        stats.fallback_model = fallback_used
        stats.status = "need_confirm" if pending_actions else "ok"

        return ChatResult(
            answer=last_text(messages),
            decision=result.get("rag_decision"),
            messages=messages,
            thread_id=resolved_thread,
            pending_actions=pending_actions,
            turn_messages=turn_messages,
            trace_id=resolved_trace,
            tokens=tokens,
            model_calls=model_calls,
            daily_tokens=int(result.get("daily_tokens") or 0),
            degraded=degraded,
            fallback_model=fallback_used,
        )
    except BaseException:
        # 异常路径也要有汇总：出问题的那一轮恰恰是最需要排查的一轮
        stats.status = "error"
        raise
    finally:
        spans.finish(stats)


def last_text(messages: Sequence[BaseMessage]) -> str:
    """取最后一条**没有 tool_calls** 的 AI 消息正文（那就是给学生的答案）"""
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not message.tool_calls:
            content = message.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in content
                )
    return ""


def use_agent(
    user_input: str,
    *,
    history: Sequence[BaseMessage] | None = None,
    agent: Any | None = None,
    context: AgentContext | None = None,
) -> str:
    """兼容旧签名的薄封装：只要答案文本"""
    return chat(user_input, history=history, agent=agent, context=context).answer


def warmup() -> None:
    """预热：把检索侧的模型和连接提前加载好（约 6~10 秒）

    放在 agent 启动时调一次，否则第一个学生的问题要干等。
    失败不抛：预热失败只影响首个问题的延迟，不该挡住整个服务启动。
    """
    from app.tool import warmup_knowledge_base

    try:
        warmup_knowledge_base()
    except Exception as exc:                      # 数据库/向量库没起来时也能先跑通对话
        logger.warning("知识库预热失败（不影响对话，只是首个检索会慢）：{}", exc)


def _cli_user_id() -> int | None:
    """从命令行取演示用户：`python -m app.harness.agent --user 7`

    不传就是匿名（不开记忆、预约类工具会回「请先登录」），行为和以前一致。
    """
    argv = sys.argv[1:]
    if "--user" not in argv:
        return None
    try:
        return int(argv[argv.index("--user") + 1])
    except (IndexError, ValueError):
        logger.warning("--user 后面要跟数字用户ID，已忽略")
        return None


def _handle_pending(result: ChatResult, context: AgentContext) -> None:
    """命令行里把「待确认」的写操作走完一遍（等价于 FastAPI 的 /confirm 接口）"""
    from app.harness.confirm import confirm_action

    for action in result.pending_actions:
        print(f"  [待确认] {action.summary}")
        try:
            answer = input("确认提交吗？(y/N) ")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        approve = answer.strip().lower() in {"y", "yes", "是", "确认", "好"}
        confirmed = confirm_action(
            tool_call_id=action.tool_call_id,
            user_id=context.user_id,
            approve=approve,
            thread_id=result.thread_id,
            user_name=context.user_name,
            user_role=context.user_role,
        )
        print(f"  [{confirmed.status}] {confirmed.answer}")


def _main() -> None:
    """命令行：python -m app.harness.agent [--user 7]"""
    from app.observability import setup_logging

    setup_logging()                       # 控制台 + logs/app-*.jsonl（带 trace_id）
    user_id = _cli_user_id()
    context = AgentContext(
        user_id=user_id,
        user_name="演示学生" if user_id else "",
        session_id="cli",
    )
    logger.info(
        "模型：{}｜user_id={}｜记忆：{}",
        describe_models(),
        user_id,
        "开" if user_id else "关（匿名，单轮）",
    )
    warmup()
    runner = build_agent()
    while True:
        try:
            user_input = input("User: ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user_input.strip().lower() in {"exit", "quit", "q"}:
            break
        if not user_input.strip():
            continue

        result = chat(user_input, agent=runner, context=context)
        decision = result.decision
        if decision is not None:
            print(
                f"  [决策] {decision.stage}"
                f"｜放行={'是' if decision.allow_rag else '否'}"
                f"｜规则={decision.rule.action}({decision.rule.score})"
                f"｜{decision.latency_ms:.0f}ms"
            )
        print(f"Assistant: {result.answer}")
        if result.thread_id:
            print(f"  [会话] {result.thread_id}")
        _handle_pending(result, context)


# ---------------------------------------------------------------- 自检
#
#     .\\.venv\\Scripts\\python.exe -m app.harness.agent --self-check
#
# 不连模型、不连数据库：把 build_agent / get_fallback_model 换成假的，
# 只验证「降级」这一条的四种走法（成功 / 未配置 / 备用也挂 / 配置错误不降级）。


def _self_check() -> None:  # pragma: no cover - 脚本式自检
    real_build_agent = globals()["build_agent"]
    real_fallback = globals()["get_fallback_model"]
    real_fallback_name = globals()["fallback_model_name"]

    ctx = AgentContext(user_id=1, user_name="自检同学")
    payload = {"messages": [HumanMessage(content="你好")]}
    config = {"metadata": {}}

    class Boom(RuntimeError):
        pass

    class PrimaryFails:
        def invoke(self, *_args, **_kwargs):
            raise Boom("主模型连接失败")

    ok_result = {"messages": [AIMessage(content="备用模型答的")], "turn_tokens": 10}

    try:
        # ① 主模型正常 → 不降级
        class PrimaryOk:
            def invoke(self, *_args, **_kwargs):
                return {"messages": [AIMessage(content="主模型答的")], "turn_tokens": 5}

        result, degraded, used = _invoke_with_fallback(PrimaryOk(), payload, ctx, config)
        assert result is not None and not degraded and used is None
        logger.info("降级自检：正常路径不降级")

        # ② 主模型失败 + 没配备用模型 → 原样抛（让运维看到真实错误）
        globals()["get_fallback_model"] = lambda: None
        globals()["fallback_model_name"] = lambda: None
        try:
            _invoke_with_fallback(PrimaryFails(), payload, ctx, config)
            raise AssertionError("未配置备用模型时不应该静默成功")
        except Boom:
            pass
        logger.info("降级自检：未配置备用模型 → 原样抛出")

        # ③ 主模型失败 + 备用模型可用 → 降级成功
        globals()["get_fallback_model"] = lambda: object()
        globals()["fallback_model_name"] = lambda: "test:fallback"
        globals()["build_agent"] = lambda **_kw: PrimaryOk()
        result, degraded, used = _invoke_with_fallback(PrimaryFails(), payload, ctx, config)
        assert result is not None and degraded and used == "test:fallback", (result, degraded, used)
        assert result["messages"][-1].content == "主模型答的"
        logger.info("降级自检：主模型失败 → 备用模型接管（{}）", used)

        # ④ 主备都挂 → 返回 None，由 chat() 生成兜底话术
        class FallbackFails:
            def invoke(self, *_args, **_kwargs):
                raise Boom("备用模型也挂了")

        globals()["build_agent"] = lambda **_kw: FallbackFails()
        result, degraded, used = _invoke_with_fallback(PrimaryFails(), payload, ctx, config)
        assert result is None and degraded and used == "test:fallback"
        logger.info("降级自检：主备都失败 → 交给兜底话术")

        # ⑤ 配置错误（ModelConfigError）不降级，交给 503 处理器
        class ConfigBroken:
            def invoke(self, *_args, **_kwargs):
                raise ModelConfigError("缺少 DEEPSEEK_API_KEY")

        try:
            _invoke_with_fallback(ConfigBroken(), payload, ctx, config)
            raise AssertionError("ModelConfigError 不应该被降级吞掉")
        except ModelConfigError:
            pass
        logger.info("降级自检：配置错误不降级（保持 503）")
    finally:
        globals()["build_agent"] = real_build_agent
        globals()["get_fallback_model"] = real_fallback
        globals()["fallback_model_name"] = real_fallback_name

    logger.info("主模型降级自检通过")


if __name__ == "__main__":
    if "--self-check" in sys.argv[1:]:
        _self_check()
    else:
        _main()
