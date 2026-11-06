"""Agent 端到端评测：20 条用例 + 规则打分 + 基线对比

    .\\.venv\\Scripts\\python.exe scripts\\eval_agent.py
    .\\.venv\\Scripts\\python.exe scripts\\eval_agent.py --category rule_qa
    .\\.venv\\Scripts\\python.exe scripts\\eval_agent.py --limit 5            # 省钱模式
    .\\.venv\\Scripts\\python.exe scripts\\eval_agent.py --baseline           # 存基线
    .\\.venv\\Scripts\\python.exe scripts\\eval_agent.py --diff               # 与基线对比

**为什么需要它**

`app/rag/eval_retrieval.py` 只看「检索层」的召回/MRR，回答不了"改完提示词、调完决策阈值、
加了降级之后，整体是变好还是变坏"。这个脚本补的是**端到端**那一层：真的跑 `chat()`，
按规则给每条用例打分。

**为什么用规则打分，不做 LLM 裁判**

规则（关键词 / 工具是否被调用 / 是否停在待确认）已经能抓住绝大多数回归，
而且免费、可复现。等规则分不出好坏时再加裁判模型 —— 那属于下一档。

**五类用例**（见 data/eval/eval_agent.jsonl）

    rule_qa        规章问答：必须查库（tools 里有 retrieval_knowledge_base）
    off_topic      闲聊/计算/编程：**不该**查库
    booking_query  预约查询：必须调查询工具（search_labs / list_my_reservations）
    booking_write  预约写操作：必须停在 need_confirm，且**绝不能**说"预约成功"
    injection      注入/越权：不能泄露系统提示、不能吐手机号

**一条硬性保证：评测不写业务库**

写操作在中间件里就被拦成「待确认」了（app/harness/confirm.py），评测全程不调 confirm，
所以不会创建预约。脚本还会在跑前跑后各数一次 `lab_reservations` / `library_reservations`，
数量变了就直接判整体失败 —— 防止将来某次改动让写操作"漏"出去。

**跑之前要知道的两件事**

1. **rule_qa 类需要 Milvus + 知识库索引**。Milvus 没起时检索会报错，模型会如实说
   "暂时查不到，不凭印象答" —— 这 8 条会判失败，**这是正确行为**，不是 agent 坏了。
   基线文件里记了 `environment`，`--diff` 会提示两次结果是不是同一个环境。
2. **评测会绕开预算层**（`budget.enabled = False`）：一轮 20 条约 30 万 token，
   正好等于默认日配额；不绕开的话跑到一半后面的用例全部拿到"额度已用完"的兜底话术，
   看起来像模型突然变傻。绕开之后也不会写 `agent_usage`，不污染种子数据。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage                     # noqa: E402
from sqlalchemy import func, select                              # noqa: E402

from app.database import SessionLocal                            # noqa: E402
from app.harness.agent import build_agent, chat                   # noqa: E402
from app.harness.budget import budget as budget_config            # noqa: E402
from app.harness.context import AgentContext                      # noqa: E402
from app.memory import short_term as memory_short_term            # noqa: E402
from app.observability import setup_logging                       # noqa: E402
from app.services.laboratory.table import LabReservation          # noqa: E402
from app.services.library.table import SeatReservation            # noqa: E402

CASES_PATH = ROOT / "data" / "eval" / "eval_agent.jsonl"
# 基线放在 scripts/ 下：它是**生成物**（模型有抖动，跨机器比对意义不大），
# 且 data/ 在只读挂载的环境里写不进去。
BASELINE_PATH = ROOT / "scripts" / "eval_baseline_agent.json"
MANIFEST_PATH = ROOT / "scripts" / "seed_manifest.json"

OK = "OK"
FAIL = "FAIL"


# ==================================================================
# 数据结构
# ==================================================================
@dataclass
class CaseResult:
    case_id: int
    category: str
    question: str
    status: str
    decision: str
    tools: list[str] = field(default_factory=list)
    answer: str = ""
    elapsed_ms: float = 0.0
    failures: list[str] = field(default_factory=list)
    note: str = ""                             # 例如「重试后通过」

    @property
    def passed(self) -> bool:
        return not self.failures


# ==================================================================
# 读取与打分
# ==================================================================
def load_cases(path: Path, *, category: str | None, limit: int | None) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            cases.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{line_no} JSON 解析失败：{exc}") from exc

    if category:
        cases = [case for case in cases if case.get("category") == category]
    if limit:
        cases = cases[:limit]
    return cases


def tools_used(result: Any) -> list[str]:
    """这一轮模型实际调用了哪些工具（按调用顺序，去重前）"""
    names: list[str] = []
    for message in result.messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls or []:
                name = call.get("name")
                if name:
                    names.append(str(name))
    return names


def check_case(case: dict[str, Any], result: Any, tools: list[str]) -> list[str]:
    """按 expect 里声明的规则逐条打分，返回失败原因列表（空 = 通过）"""
    expect = case.get("expect") or {}
    failures: list[str] = []

    status = "need_confirm" if result.pending_actions else "ok"
    if "status" in expect and status != expect["status"]:
        failures.append(f"status={status}，期望 {expect['status']}")

    answer = result.answer or ""
    for keyword in expect.get("must_contain_all") or []:
        if keyword not in answer:
            failures.append(f"答案缺少关键词：{keyword}")
    any_words = expect.get("must_contain_any") or []
    if any_words and not any(word in answer for word in any_words):
        failures.append(f"答案未命中任一关键词：{any_words}")
    for keyword in expect.get("must_not_contain") or []:
        if keyword in answer:
            failures.append(f"答案不该出现：{keyword}")

    tool_set = set(tools)
    for name in expect.get("tools") or []:
        if name not in tool_set:
            failures.append(f"未调用工具：{name}（实际：{sorted(tool_set)}）")
    for name in expect.get("tools_forbidden") or []:
        if name in tool_set:
            failures.append(f"不该调用工具：{name}")

    if expect.get("pending") is True and not result.pending_actions:
        failures.append("期望返回待确认动作，实际直接作答")

    decision = getattr(result.decision, "stage", None) or "-"
    allowed = expect.get("decision_in") or []
    if allowed and decision not in allowed:
        failures.append(f"决策 stage={decision}，期望其中之一 {allowed}")

    return failures


# ==================================================================
# 运行
# ==================================================================
def _reservation_counts() -> tuple[int, int]:
    with SessionLocal() as db:
        lab = int(db.scalar(select(func.count()).select_from(LabReservation)) or 0)
        lib = int(db.scalar(select(func.count()).select_from(SeatReservation)) or 0)
    return lab, lib


def _eval_user() -> tuple[int, str]:
    """优先用种子里的主测试学生（这样 list_my_reservations 有数据可查）"""
    if MANIFEST_PATH.exists():
        try:
            manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            info = (manifest.get("users") or {}).get("20210001") or {}
            if info.get("id"):
                return int(info["id"]), str(info.get("name") or "评测同学")
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return 1, "评测同学"


def _environment() -> dict[str, str]:
    """记录跑评测时的依赖状态

    基线里带上这个，`--diff` 才知道两次结果是不是同一个环境 ——
    Milvus 没起时 rule_qa 全靠模型瞎猜，拿这种基线和"有知识库"的结果比没有意义。
    """
    try:
        from app.api.health import _knowledge_state, _memory_state

        return {"memory": _memory_state(), "knowledge": _knowledge_state()}
    except Exception as exc:                    # noqa: BLE001 —— 探针失败不影响评测
        return {"memory": f"unknown({type(exc).__name__})", "knowledge": "unknown"}


def _run_one(runner: Any, context: AgentContext, case: dict[str, Any], index: int) -> CaseResult:
    """跑一条用例并打分（异常也折成一条失败结果，不中断整轮）"""
    question = str(case.get("question") or "")
    case_id = int(case.get("id") or index)
    started = time.perf_counter()
    try:
        outcome = chat(question, agent=runner, context=context)
        elapsed = (time.perf_counter() - started) * 1000
        tools = tools_used(outcome)
        return CaseResult(
            case_id=case_id,
            category=str(case.get("category") or "-"),
            question=question,
            status="need_confirm" if outcome.pending_actions else "ok",
            decision=getattr(outcome.decision, "stage", None) or "-",
            tools=tools,
            answer=outcome.answer or "",
            elapsed_ms=elapsed,
            failures=check_case(case, outcome, tools),
        )
    except Exception as exc:                    # noqa: BLE001 —— 单条崩了不该中断整轮评测
        elapsed = (time.perf_counter() - started) * 1000
        return CaseResult(
            case_id=case_id,
            category=str(case.get("category") or "-"),
            question=question,
            status="error",
            decision="-",
            elapsed_ms=elapsed,
            failures=[f"调用异常：{type(exc).__name__}: {exc}"],
        )


def run_cases(cases: list[dict[str, Any]], *, retry_failed: int = 0) -> list[CaseResult]:
    """跑全部用例；`retry_failed` > 0 时对失败的用例再试几次

    为什么要重试：模型有抖动，同一条「我都有哪些预约？」可能这次调了工具、下次直接答了。
    单次结果适合当告警，不适合当质量指标。重试的语义是「给它 N 次机会，过了就算过」，
    并在输出里标出哪些是重试后才通过的 —— 那本身就是"不稳定"的证据。
    """
    user_id, user_name = _eval_user()
    runner = build_agent()                     # 复用一个 agent，省掉每轮重建
    context = AgentContext(user_id=user_id, user_name=user_name, session_id="eval")

    # 评测不读写 Milvus 会话记忆：B.3 明确要求「评测本身不往 MySQL / Milvus
    # 写任何测试痕迹」。直接关掉 short_term 的开关，比给每条用例造独立 thread
    # 更彻底 —— 否则用例之间会互相带上历史，重复跑还会让同一个会话越来越长。
    original_memory = memory_short_term.MEMORY_ENABLED
    memory_short_term.MEMORY_ENABLED = False
    try:
        results = [
            _run_one(runner, context, case, index)
            for index, case in enumerate(cases, start=1)
        ]

        if retry_failed > 0:
            by_id = {
                int(case.get("id") or i): case
                for i, case in enumerate(cases, start=1)
            }
            for position, result in enumerate(results):
                if result.passed:
                    continue
                for _ in range(retry_failed):
                    retry = _run_one(
                        runner, context, by_id[result.case_id], result.case_id
                    )
                    if retry.passed:
                        retry.note = "重试后通过"
                        results[position] = retry
                        break
        return results
    finally:
        memory_short_term.MEMORY_ENABLED = original_memory


# ==================================================================
# 输出
# ==================================================================
def print_results(results: list[CaseResult]) -> None:
    width = max(len(result.question) for result in results) + 2
    for result in results:
        mark = OK if result.passed else FAIL
        question = result.question if len(result.question) <= width else result.question[: width - 1] + "…"
        print(f"[{mark}] {result.case_id:>2} {result.category:<14} {question:<{width}} "
              f"{result.elapsed_ms / 1000:>5.1f}s  {result.status:<12} {result.decision}"
              + (f"  （{result.note}）" if result.note else ""))
        for failure in result.failures:
            print(f"        └─ {failure}")


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    passed = [r for r in results if r.passed]
    by_category: dict[str, list[CaseResult]] = defaultdict(list)
    for result in results:
        by_category[result.category].append(result)

    latencies = sorted(r.elapsed_ms for r in results if r.elapsed_ms > 0)
    p50 = statistics.median(latencies) if latencies else 0.0
    p95 = latencies[max(0, int(len(latencies) * 0.95) - 1)] if latencies else 0.0

    return {
        "total": len(results),
        "passed": len(passed),
        "failed": len(results) - len(passed),
        "by_category": {
            category: {"total": len(items), "passed": sum(1 for i in items if i.passed)}
            for category, items in by_category.items()
        },
        "latency_p50_ms": round(p50, 1),
        "latency_p95_ms": round(p95, 1),
    }


def print_summary(summary: dict[str, Any]) -> None:
    print()
    print(f"通过 {summary['passed']}/{summary['total']}"
          f"（失败 {summary['failed']}）｜"
          f"延迟 P50={summary['latency_p50_ms']:.0f}ms P95={summary['latency_p95_ms']:.0f}ms")
    for category, stat in sorted(summary["by_category"].items()):
        print(f"  {category:<16} {stat['passed']}/{stat['total']}")


def write_baseline(
    results: list[CaseResult], summary: dict[str, Any], path: Path, env: dict[str, str]
) -> None:
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "environment": env,
        "summary": summary,
        "cases": {
            str(result.case_id): {
                "passed": result.passed,
                "status": result.status,
                "decision": result.decision,
                "tools": result.tools,
                "elapsed_ms": round(result.elapsed_ms, 1),
                "failures": result.failures,
            }
            for result in results
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n基线已写入：{path.relative_to(ROOT)}")


def diff_with_baseline(results: list[CaseResult], path: Path) -> int:
    """返回 1 表示有回归（变差），0 表示没有"""
    if not path.exists():
        print(f"\n没有基线文件 {path.relative_to(ROOT)}，先跑一次 --baseline")
        return 1

    baseline = json.loads(path.read_text(encoding="utf-8"))
    old = baseline.get("cases") or {}
    old_env = baseline.get("environment") or {}
    improved: list[int] = []
    regressed: list[int] = []
    for result in results:
        previous = old.get(str(result.case_id))
        if previous is None:
            continue
        was = bool(previous.get("passed"))
        if was and not result.passed:
            regressed.append(result.case_id)
        elif not was and result.passed:
            improved.append(result.case_id)

    print(f"\n与基线（{baseline.get('generated_at')}）对比：")
    print(f"  基线环境：{old_env or '未知'}")
    if old_env and old_env.get("knowledge") != "ok":
        print("  ⚠ 基线是在知识库不可用时生成的（rule_qa 类结果不可比）")
    print(f"  变好：{improved or '无'}")
    print(f"  变差：{regressed or '无'}")
    if regressed:
        for case_id in regressed:
            match = next((r for r in results if r.case_id == case_id), None)
            if match:
                print(f"  - #{case_id} {match.question}：{match.failures}")
    return 1 if regressed else 0


# ==================================================================
# 入口
# ==================================================================
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent 端到端评测（规则打分）")
    parser.add_argument("--cases", default=str(CASES_PATH), help="评测集路径（JSONL）")
    parser.add_argument("--category", help="只跑某一类：rule_qa / off_topic / ...")
    parser.add_argument("--limit", type=int, help="只跑前 N 条（省钱）")
    parser.add_argument("--baseline", action="store_true", help="把本次结果存成基线")
    parser.add_argument("--diff", action="store_true", help="与基线对比，有回归则退出码 1")
    parser.add_argument(
        "--retry-failed", type=int, default=0,
        help="失败的用例再试几次（模型抖动；过了就算过，并标注「重试后通过」）",
    )
    parser.add_argument("--json-out", help="把本次结果写成 JSON")
    args = parser.parse_args(argv)

    cases_path = Path(args.cases)
    if not cases_path.exists():
        print(f"找不到评测集：{cases_path}")
        return 2

    cases = load_cases(cases_path, category=args.category, limit=args.limit)
    if not cases:
        print("没有匹配的用例")
        return 2

    # 评测**不是**学生请求，必须绕开预算层：
    # 一轮 20 条实测约 30 万 token，正好等于默认日配额（HARNESS_DAILY_TOKENS=300000），
    # 跑到一半后面的用例就会全部拿到「今天额度已用完」的兜底话术
    # （表现是 0.3s 返回、答非所问，看日志才知道是配额）。
    # 关掉之后也不会写 agent_usage，不会污染种子数据。
    budget_config.enabled = False

    # 评测本身也走 chat()，日志会带 trace_id；这里不需要落盘 JSONL。
    # 级别压到 CRITICAL：Milvus 没起时每条 rule_qa 都会刷一大段检索栈，
    # 而结论已经由下面的打分表给出，不需要再重复读栈。
    setup_logging(jsonl=False, level="CRITICAL")

    env = _environment()
    before = _reservation_counts()
    print(f"评测集：{cases_path.name}｜{len(cases)} 条｜"
          f"依赖状态：{env['memory']}/{env['knowledge']}｜"
          f"不写业务库、不写 Milvus 记忆（跑前预约数 {before[0]}+{before[1]}）\n")

    results = run_cases(cases, retry_failed=args.retry_failed)
    print_results(results)
    summary = summarize(results)
    print_summary(summary)

    after = _reservation_counts()
    if after != before:
        print(f"\n[FAIL] 评测期间预约数发生变化：{before} -> {after}，"
              "说明写操作漏出去了（本应停在待确认）")
        summary["write_leak"] = True

    if args.baseline:
        write_baseline(results, summary, BASELINE_PATH, env)

    exit_code = 0
    if summary["failed"] or summary.get("write_leak"):
        exit_code = 1
    if args.diff:
        exit_code = max(exit_code, diff_with_baseline(results, BASELINE_PATH))

    if args.json_out:
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "summary": summary,
            "cases": [
                {
                    "id": r.case_id,
                    "category": r.category,
                    "question": r.question,
                    "passed": r.passed,
                    "status": r.status,
                    "decision": r.decision,
                    "tools": r.tools,
                    "elapsed_ms": round(r.elapsed_ms, 1),
                    "failures": r.failures,
                    "answer": r.answer,
                }
                for r in results
            ],
        }
        Path(args.json_out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"结果已写入：{args.json_out}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
