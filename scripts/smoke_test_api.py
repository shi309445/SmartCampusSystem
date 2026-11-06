"""接口冒烟脚本：把 19 个 FastAPI 路由（含主要错误分支）逐条打一遍

前提：先跑过 `scripts/seed_test_data.py`（它生成 seed_manifest.json 和账号），
并且服务已经起在 --base-url 上。

    .\\.venv\\Scripts\\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000
    .\\.venv\\Scripts\\python.exe scripts\\smoke_test_api.py
    .\\.venv\\Scripts\\python.exe scripts\\smoke_test_api.py --skip-chat   # 不花钱调模型

**覆盖范围**

| # | 方法   | 路径                          | 验证点 |
|---|--------|-------------------------------|--------|
| 1 | GET    | /                             | 服务信息 |
| 2 | GET    | /health                       | 数据库连通 |
| 3 | POST   | /api/auth/register            | 注册成功 / 账号重复 409 |
| 4 | POST   | /api/auth/login               | 登录成功 / 密码错 401 / 账号禁用 403 |
| 5 | GET    | /api/auth/me                  | 正常 / 无令牌 401 / 假令牌 401 |
| 6 | PUT    | /api/auth/profile             | 改资料 |
| 7 | PUT    | /api/auth/password            | 改密码 / 原密码错 400 |
| 8 | POST   | /api/chat                     | 一轮问答 / 空消息 422 |
| 9 | POST   | /api/chat/confirm             | 找不到 404 / 兜底回放取消 200 |
| 10| GET    | /api/chat/history             | 会话历史 |
| 11| GET    | /api/chat/threads             | 会话列表 |
| 12| DELETE | /api/chat/thread              | 清空会话 |
| 13| GET    | /api/admin/usage/{user_id}    | 管理员 200 / 学生 403 / 无令牌 401 / 参数错 422 |

**结果怎么读**

    OK    这条通过
    FAIL  这条不符合预期（脚本最后以退出码 1 结束，可以直接接 CI）
    SKIP  前置条件不满足（例如没写会话历史 / 不想调模型）
    DEGR  依赖不可用但接口本身没崩（例如模型没配好返回 503）——不算失败，会单独统计

有写操作的分支都挑「不会破坏种子数据」的方向走：
    * 改密码改完会立刻改回去；
    * /api/chat/confirm 用 approve=false（拒绝），且用一条独立的 probe 会话，不动种子里的待确认动作；
    * 清空会话清的是 demo-deletable 这条专门用来删的会话，重跑种子脚本会补回来。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx                                              # noqa: E402

MANIFEST_PATH = Path(__file__).resolve().parent / "seed_manifest.json"

OK = "OK"
FAIL = "FAIL"
SKIP = "SKIP"
DEGRADED = "DEGR"

Checker = Callable[[int, Any], tuple[bool, str]]


@dataclass
class Case:
    """一条待验证的请求"""

    name: str
    method: str
    path: str
    expect_status: int = 200
    json_body: Any = None
    params: dict[str, Any] | None = None
    headers: dict[str, str] | None = None
    check: Checker | None = None
    skip_reason: str | None = None
    # 依赖不可用时允许降级（目前只有 /api/chat 会这样：模型没配好 -> 503）
    allow_degraded: tuple[int, ...] = ()
    # 执行前先做一次准备请求（例如「先查一个待审核的预约，再拿它的 id 去审核」），
    # 可以就地改写 json_body / path / skip_reason
    prepare: Callable[["httpx.Client", "Case"], None] | None = None


@dataclass
class Result:
    case: Case
    status: int | None
    outcome: str
    detail: str = ""
    body: Any = field(default=None, repr=False)


# ==================================================================
# 通用断言
# ==================================================================
def _envelope_ok(status: int, body: Any) -> tuple[bool, str]:
    """统一信封检查：{code, message, data} 且 code 与 HTTP 状态码一致"""
    if not isinstance(body, dict) or "code" not in body:
        return False, f"返回不是统一信封：{str(body)[:120]}"
    if int(body["code"]) != status:
        return False, f"信封 code={body['code']} 与 HTTP {status} 不一致"
    return True, ""


def _data(body: Any) -> Any:
    return body.get("data") if isinstance(body, dict) else None


def make_check(
    *,
    data_assert: Callable[[Any], tuple[bool, str]] | None = None,
    message_contains: str | None = None,
) -> Checker:
    """组合出常见的断言：信封合法 -> （可选）data 断言 -> （可选）message 关键词"""

    def _check(status: int, body: Any) -> tuple[bool, str]:
        ok, detail = _envelope_ok(status, body)
        if not ok:
            return ok, detail
        if message_contains:
            message = str(body.get("message") or "")
            if message_contains not in message:
                return False, f"message 里没有「{message_contains}」，实际：{message}"
        if data_assert is not None:
            return data_assert(_data(body))
        return True, ""

    return _check


def _expect_data(**_fields: Any) -> Callable[[Any], tuple[bool, str]]:
    """断言 data 里的若干字段等于期望值"""

    def _assert(data: Any) -> tuple[bool, str]:
        if not isinstance(data, dict):
            return False, f"data 不是对象：{str(data)[:80]}"
        for key, expected in _fields.items():
            actual = data.get(key)
            if actual != expected:
                return False, f"data.{key}={actual!r}，期望 {expected!r}"
        return True, ""

    return _assert


def _expect_data_where(**checks: Callable[[Any], bool]) -> Callable[[Any], tuple[bool, str]]:
    """断言 data 里的字段满足某个条件"""

    def _assert(data: Any) -> tuple[bool, str]:
        if not isinstance(data, dict):
            return False, f"data 不是对象：{str(data)[:80]}"
        for key, predicate in checks.items():
            if not predicate(data.get(key)):
                return False, f"data.{key}={data.get(key)!r} 不满足条件"
        return True, ""

    return _assert


# ==================================================================
# 请求执行
# ==================================================================
def run_case(client: httpx.Client, case: Case) -> Result:
    if case.prepare is not None and not case.skip_reason:
        try:
            case.prepare(client, case)
        except httpx.HTTPError as exc:
            return Result(case, None, FAIL, f"准备请求失败：{exc}")

    if case.skip_reason:
        return Result(case, None, SKIP, case.skip_reason)

    try:
        response = client.request(
            case.method,
            case.path,
            json=case.json_body,
            params=case.params,
            headers=case.headers,
        )
    except httpx.HTTPError as exc:
        return Result(case, None, FAIL, f"请求失败：{exc}")

    try:
        body = response.json()
    except ValueError:
        body = response.text

    status = response.status_code
    if status != case.expect_status:
        if status in case.allow_degraded:
            return Result(case, status, DEGRADED, f"依赖不可用，返回 {status}")
        detail = ""
        if isinstance(body, dict):
            detail = str(body.get("message") or "")
        return Result(case, status, FAIL, f"HTTP {status}，期望 {case.expect_status}。{detail}")

    if case.check is not None:
        ok, detail = case.check(status, body)
        return Result(case, status, OK if ok else FAIL, detail, body)

    ok, detail = _envelope_ok(status, body)
    return Result(case, status, OK if ok else FAIL, detail, body)


# ==================================================================
# 用例编排
# ==================================================================
def build_cases(manifest: dict[str, Any], *, skip_chat: bool) -> list[Case]:
    users = manifest["users"]
    passwords = manifest["passwords"]
    memory = manifest.get("memory") or {}

    student_id = users["20210001"]["id"]
    disabled_id = users["20210003"]["id"]
    student_thread = f"u{student_id}"
    pending_thread = (memory.get("threads") or {}).get("pending", f"u{student_id}:demo-pending")
    deletable_thread = (memory.get("threads") or {}).get(
        "deletable", f"u{student_id}:demo-deletable"
    )
    probe_reject_thread = memory.get("probe_thread", f"u{student_id}:probe-confirm")

    cases: list[Case] = []
    add = cases.append

    # ---------------------------------------------------------- 系统
    add(Case(
        "GET  /  服务信息", "GET", "/",
        check=make_check(data_assert=_expect_data_where(
            name=lambda v: isinstance(v, str) and "智能校园助手" in v,
        )),
    ))
    add(Case(
        "GET  /health  健康检查", "GET", "/health",
        check=make_check(data_assert=_expect_data(
            database="ok", version="0.1.0",
        )),
    ))
    add(Case(
        # 依赖不可用（Milvus 没起）时 status 应该是 degraded，而不是硬报 ok
        "GET  /health  各项依赖状态取值合法", "GET", "/health",
        check=make_check(data_assert=_expect_data_where(
            status=lambda v: v in ("ok", "degraded"),
            database=lambda v: v in ("ok", "down"),
            memory=lambda v: v in ("ok", "down", "missing", "off"),
            knowledge=lambda v: v in ("ok", "down", "missing"),
            model=lambda v: v in ("ok", "missing", "error"),
            agent=lambda v: v in ("ready", "not_ready"),
        )),
    ))

    # ---------------------------------------------------------- 认证
    add(Case(
        "POST /api/auth/register  账号已存在 -> 409", "POST", "/api/auth/register",
        expect_status=409,
        json_body={"username": "probe01", "password": passwords["probe"]},
        check=make_check(message_contains="已存在"),
    ))
    add(Case(
        "POST /api/auth/register  参数校验 -> 422", "POST", "/api/auth/register",
        expect_status=422,
        json_body={"username": "ab", "password": "123"},     # 账号太短 + 密码太短
        check=make_check(message_contains="参数校验"),
    ))
    fresh_username = f"probe_reg_{datetime.now():%Y%m%d%H%M%S}"
    add(Case(
        "POST /api/auth/register  注册成功", "POST", "/api/auth/register",
        json_body={"username": fresh_username, "password": "Probe@123456", "name": "临时注册账号"},
        check=make_check(data_assert=_expect_data(
            username=fresh_username, role="student", status=1,
        )),
    ))

    add(Case(
        "POST /api/auth/login  密码错误 -> 401", "POST", "/api/auth/login",
        expect_status=401,
        json_body={"username": "20210001", "password": "wrong-password"},
        check=make_check(message_contains="账号或密码错误"),
    ))
    add(Case(
        "POST /api/auth/login  账号被禁用 -> 403", "POST", "/api/auth/login",
        expect_status=403,
        json_body={"username": "20210003", "password": passwords["student"]},
        check=make_check(message_contains="禁用"),
    ))

    # token 在下面由主流程登录后注入（这些 case 的 headers 会被替换）
    add(Case(
        "POST /api/auth/login  学生登录成功", "POST", "/api/auth/login",
        json_body={"username": "20210001", "password": passwords["student"]},
        check=make_check(data_assert=_expect_data_where(
            token=lambda v: isinstance(v, str) and len(v) > 20,
            token_type=lambda v: v == "bearer",
            user=lambda v: isinstance(v, dict) and v.get("username") == "20210001",
        )),
    ))

    add(Case("GET  /api/auth/me  未登录 -> 401", "GET", "/api/auth/me", expect_status=401,
             check=make_check(message_contains="登录")))
    add(Case("GET  /api/auth/me  假令牌 -> 401", "GET", "/api/auth/me", expect_status=401,
             headers={"Authorization": "Bearer not-a-real-token"},
             check=make_check(message_contains="令牌")))
    add(Case("GET  /api/auth/me  当前用户", "GET", "/api/auth/me",
             check=make_check(data_assert=_expect_data(
                 username="20210001", name="张小明", role="student", status=1,
             ))))

    add(Case("PUT  /api/auth/profile  改资料", "PUT", "/api/auth/profile",
             json_body={"name": "冒烟测试改名", "email": "smoke@lut.edu.cn"},
             check=make_check(data_assert=_expect_data(
                 name="冒烟测试改名", email="smoke@lut.edu.cn",
             ))))
    add(Case("PUT  /api/auth/profile  空姓名 -> 422", "PUT", "/api/auth/profile",
             expect_status=422, json_body={"name": "   "},
             check=make_check(message_contains="参数校验")))

    add(Case("PUT  /api/auth/password  原密码错误 -> 400", "PUT", "/api/auth/password",
             expect_status=400,
             json_body={"old_password": "definitely-wrong", "new_password": "Probe@654321"},
             check=make_check(message_contains="原密码错误")))

    # ---------------------------------------------------------- 智能助手
    add(Case(
        "POST /api/chat  空消息 -> 422", "POST", "/api/chat",
        expect_status=422, json_body={"message": ""},
        check=make_check(message_contains="参数校验"),
    ))
    if skip_chat:
        add(Case("POST /api/chat  一轮问答", "POST", "/api/chat", skip_reason="--skip-chat 未调用模型"))
    else:
        add(Case(
            "POST /api/chat  一轮问答", "POST", "/api/chat",
            json_body={"message": "你好，简单介绍一下你能做什么"},
            allow_degraded=(503,),
            check=make_check(data_assert=_expect_data_where(
                status=lambda v: v in ("ok", "need_confirm"),
                answer=lambda v: isinstance(v, str) and len(v) > 0,
                thread_id=lambda v: isinstance(v, str) and v.startswith(f"u{student_id}"),
            )),
        ))

    add(Case(
        "POST /api/chat/confirm  找不到待确认动作 -> 404", "POST", "/api/chat/confirm",
        expect_status=404,
        json_body={
            "thread_id": probe_reject_thread,
            "tool_call_id": "call_not_exists_at_all",
            "approve": True,
        },
        check=make_check(message_contains="没有找到"),
    ))
    add(Case(
        "POST /api/chat/confirm  thread_id 不属于当前账号 -> 403", "POST", "/api/chat/confirm",
        expect_status=403,
        json_body={
            "thread_id": f"u{student_id + 9999}:probe-confirm",   # 别人（不存在）的会话
            "tool_call_id": "call_whatever",
            "approve": True,
        },
        check=make_check(),
    ))
    add(Case(
        "POST /api/chat/confirm  兜底回放并拒绝 -> 200", "POST", "/api/chat/confirm",
        json_body={
            "thread_id": probe_reject_thread,
            "tool_call_id": "call_probe_reject_0001",
            "approve": False,
            "action": {
                "tool_name": "cancel_my_reservation",
                "args": {"affair_name": "lab", "reservation_id": 1, "reason": "冒烟测试"},
            },
        },
        check=make_check(data_assert=_expect_data(status="rejected", ok=False)),
    ))

    main_thread = (memory.get("threads") or {}).get("main", student_thread)
    history_skip = None if memory.get("seeded") else "Milvus 未写入（先启动 Milvus 再跑 seed）"
    add(Case(
        "GET  /api/chat/history  会话历史", "GET", "/api/chat/history",
        skip_reason=history_skip,
        params={"session_id": "demo-pending"},
        check=make_check(data_assert=_expect_data_where(
            total=lambda v: isinstance(v, int) and v > 0,
            messages=lambda v: isinstance(v, list) and len(v) > 0,
        )),
    ))
    add(Case(
        "GET  /api/chat/history  默认长会话（无数据也应为空列表）", "GET", "/api/chat/history",
        check=make_check(data_assert=_expect_data_where(
            thread_id=lambda v: v == main_thread,
            total=lambda v: isinstance(v, int) and v >= 0,
            messages=lambda v: isinstance(v, list),
        )),
    ))
    add(Case(
        "GET  /api/chat/history  limit 越界 -> 422", "GET", "/api/chat/history",
        expect_status=422, params={"limit": 0},
        check=make_check(message_contains="参数校验"),
    ))
    add(Case(
        "GET  /api/chat/threads  会话列表", "GET", "/api/chat/threads",
        check=make_check(data_assert=lambda v: (
            (True, "") if isinstance(v, list) else (False, f"data 不是列表：{type(v).__name__}")
        )),
    ))
    add(Case(
        "DELETE /api/chat/thread  清空会话", "DELETE", "/api/chat/thread",
        params={"session_id": "demo-deletable"},
        check=make_check(data_assert=_expect_data_where(
            thread_id=lambda v: v == deletable_thread,
            deleted=lambda v: isinstance(v, int) and v >= 0,
        )),
    ))

    # ---------------------------------------------------------- 管理员
    # 注意：用量是**按天**记账的（agent_usage.usage_date = 今天）。
    #   * 同一天里跑过 /api/chat → tokens 会比种子值更大，所以断言「不低于」而不是「等于」；
    #   * 隔天再跑 → 今天还没有任何记录（0），种子值属于昨天，不能拿来比。
    # 所以「期望的最低值」要看种子数据是不是今天生成的，否则跨天必然误报。
    seeded_today = str(manifest.get("generated_at", ""))[:10] == str(datetime.now().date())
    min_tokens = 12345 if seeded_today else 0
    min_calls = 34 if seeded_today else 0
    add(Case(
        "GET  /api/admin/usage/{user_id}  管理员查看用量", "GET",
        f"/api/admin/usage/{student_id}",
        headers={"Authorization": "__ADMIN__"},          # 占位，主流程替换
        check=make_check(data_assert=_expect_data_where(
            user_id=lambda v: v == student_id,
            date=lambda v: v == str(datetime.now().date()),
            tokens=lambda v: isinstance(v, int) and v >= min_tokens,
            calls=lambda v: isinstance(v, int) and v >= min_calls,
            daily_tokens_limit=lambda v: isinstance(v, int) and v > 0,
            daily_calls_limit=lambda v: isinstance(v, int) and v > 0,
        )),
    ))
    add(Case(
        "GET  /api/admin/usage/{user_id}  学生访问 -> 403", "GET",
        f"/api/admin/usage/{student_id}",
        expect_status=403,
        headers={"Authorization": "__STUDENT__"},
        check=make_check(message_contains="管理员"),
    ))
    add(Case(
        "GET  /api/admin/usage/{user_id}  未登录 -> 401", "GET",
        f"/api/admin/usage/{student_id}",
        expect_status=401,
        check=make_check(message_contains="登录"),
    ))
    add(Case(
        "GET  /api/admin/usage/{user_id}  路径参数类型错误 -> 422", "GET",
        "/api/admin/usage/not-an-int",
        expect_status=422,
        headers={"Authorization": "__ADMIN__"},
        check=make_check(message_contains="参数校验"),
    ))

    # ---------------------------------------------------------- 管理员·预约审核
    #
    # 这一组会**改数据**（把待审核改成通过、再取消掉），所以设计成「先查一个待审核的」：
    #   * 第一次跑：能查到 → 审核 → 取消；
    #   * 再跑一次：没有待审核的了 → SKIP（提示重跑 seed_test_data.py 恢复）。
    # 这样既验证了写路径，又不会因为状态已被改过而误报失败。
    lab_pending: dict[str, Any] = {}
    lib_pending: dict[str, Any] = {}

    def _probe_pending(affair: str, store: dict[str, Any]):
        def _prepare(client: httpx.Client, case: Case) -> None:
            headers = case.headers or {}
            resp = client.get(
                f"/api/admin/reservations/{affair}", params={"status": 0}, headers=headers
            )
            rows: list[dict[str, Any]] = []
            if resp.status_code == 200:
                rows = ((resp.json().get("data") or {}).get("data") or [])
            if not rows:
                case.skip_reason = (
                    "没有待审核预约（种子数据已被审核过，重跑 seed_test_data.py 可恢复）"
                )
                return
            store["id"] = rows[0]["id"]
            case.path = f"/api/admin/reservations/{affair}/{store['id']}/review"

        return _prepare

    def _use_stored(affair: str, store: dict[str, Any], suffix: str):
        def _prepare(client: httpx.Client, case: Case) -> None:
            if not store.get("id"):
                case.skip_reason = "上一条审核用例被跳过，没有可用的预约 id"
                return
            case.path = f"/api/admin/reservations/{affair}/{store['id']}/{suffix}"

        return _prepare

    add(Case(
        "GET  /api/admin/reservations/lab  预约列表", "GET", "/api/admin/reservations/lab",
        headers={"Authorization": "__ADMIN__"},
        check=make_check(data_assert=_expect_data_where(
            total=lambda v: isinstance(v, int) and v >= 1,
            data=lambda v: isinstance(v, list) and all("reservation_no" in row for row in v),
        )),
    ))
    add(Case(
        "GET  /api/admin/reservations/lab  按状态过滤", "GET", "/api/admin/reservations/lab",
        headers={"Authorization": "__ADMIN__"},
        params={"status": 1, "page": 1, "page_size": 5},
        check=make_check(data_assert=_expect_data_where(
            data=lambda v: isinstance(v, list) and all(row["status"] == 1 for row in v),
        )),
    ))
    add(Case(
        "GET  /api/admin/reservations/lab  学生访问 -> 403", "GET",
        "/api/admin/reservations/lab",
        expect_status=403,
        headers={"Authorization": "__STUDENT__"},
        check=make_check(message_contains="管理员"),
    ))
    add(Case(
        "GET  /api/admin/reservations/library  预约列表", "GET",
        "/api/admin/reservations/library",
        headers={"Authorization": "__ADMIN__"},
        check=make_check(data_assert=_expect_data_where(
            total=lambda v: isinstance(v, int) and v >= 1,
        )),
    ))
    add(Case(
        "PUT  /api/admin/reservations/lab/{id}/review  审核结果非法 -> 400", "PUT",
        "/api/admin/reservations/lab/1/review",
        expect_status=400,
        json_body={"status": 3},
        headers={"Authorization": "__ADMIN__"},
        check=make_check(message_contains="审核结果只能是"),
    ))
    add(Case(
        "PUT  /api/admin/reservations/lab/{id}/review  预约不存在 -> 404", "PUT",
        "/api/admin/reservations/lab/999999/review",
        expect_status=404,
        json_body={"status": 1},
        headers={"Authorization": "__ADMIN__"},
        check=make_check(message_contains="预约不存在"),
    ))
    add(Case(
        "PUT  /api/admin/reservations/lab/{id}/review  审核通过", "PUT",
        "/api/admin/reservations/lab/0/review",     # prepare 会换成真实 id
        json_body={"status": 1, "remark": "冒烟测试：通过"},
        headers={"Authorization": "__ADMIN__"},
        prepare=_probe_pending("lab", lab_pending),
        check=make_check(data_assert=_expect_data_where(
            status=lambda v: v == 1,
            handler_name=lambda v: isinstance(v, str) and v,
        )),
    ))
    add(Case(
        "PUT  /api/admin/reservations/lab/{id}/review  重复审核 -> 409", "PUT",
        "/api/admin/reservations/lab/0/review",
        expect_status=409,
        json_body={"status": 1},
        headers={"Authorization": "__ADMIN__"},
        prepare=_use_stored("lab", lab_pending, "review"),
        check=make_check(message_contains="审核"),
    ))
    add(Case(
        "PUT  /api/admin/reservations/library/{id}/review  审核通过", "PUT",
        "/api/admin/reservations/library/0/review",
        json_body={"status": 1, "remark": "冒烟测试：通过"},
        headers={"Authorization": "__ADMIN__"},
        prepare=_probe_pending("library", lib_pending),
        check=make_check(data_assert=_expect_data_where(
            status=lambda v: v == 1,
        )),
    ))
    add(Case(
        "PUT  /api/admin/reservations/lab/{id}/cancel  管理员代取消", "PUT",
        "/api/admin/reservations/lab/0/cancel",
        json_body={"remark": "冒烟测试：管理员取消"},
        headers={"Authorization": "__ADMIN__"},
        prepare=_use_stored("lab", lab_pending, "cancel"),
        check=make_check(data_assert=_expect_data_where(
            status=lambda v: v == 3,
        )),
    ))

    return cases


# ==================================================================
# 主流程
# ==================================================================
def login(client: httpx.Client, username: str, password: str) -> str | None:
    try:
        response = client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
    except httpx.HTTPError as exc:
        print(f"  登录 {username} 失败：{exc}")
        return None
    if response.status_code != 200:
        return None
    data = response.json().get("data") or {}
    return data.get("token")


def login_any(client: httpx.Client, username: str, passwords: list[str]) -> tuple[str | None, str | None]:
    for password in passwords:
        token = login(client, username, password)
        if token:
            return token, password
    return None, None


def print_results(results: list[Result]) -> None:
    width = max(len(result.case.name) for result in results) + 2
    print()
    for index, result in enumerate(results, start=1):
        status = f"{result.status}" if result.status is not None else "-"
        print(f"[{index:>2}/{len(results)}] {result.outcome:<4} {result.case.name:<{width}} "
              f"{status:>4}  {result.detail}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="逐条验证 SmartCampusSystem 的全部 HTTP 路由")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="服务地址")
    parser.add_argument("--skip-chat", action="store_true", help="跳过 /api/chat（不调用大模型）")
    parser.add_argument("--timeout", type=float, default=180.0, help="单请求超时（秒）")
    args = parser.parse_args(argv)

    if not MANIFEST_PATH.exists():
        print(f"找不到 {MANIFEST_PATH.relative_to(ROOT)}")
        print("先执行：.\\\\.venv\\\\Scripts\\\\python.exe scripts\\\\seed_test_data.py")
        return 2

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    print(f"目标服务：{args.base_url}")
    print(f"种子数据生成于：{manifest.get('generated_at')}")

    cases = build_cases(manifest, skip_chat=args.skip_chat)

    with httpx.Client(base_url=args.base_url, timeout=args.timeout) as client:
        # ---------------------------------------------------------- 健康检查
        try:
            health = client.get("/health")
        except httpx.HTTPError as exc:
            print(f"\n服务连不上：{args.base_url}（{exc}）")
            print("先启动：.\\\\.venv\\\\Scripts\\\\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000")
            return 2
        print(f"/health -> HTTP {health.status_code} {health.text[:160]}")

        # ---------------------------------------------------------- 登录拿令牌
        student_password = manifest["users"]["20210001"]["password"]
        admin_password = manifest["users"]["admin"]["password"]
        probe_password = manifest["passwords"]["probe"]

        student_token = login(client, "20210001", student_password)
        if not student_token:
            print("学生账号登录失败，请先重跑 seed_test_data.py")
            return 2
        admin_token = login(client, "admin", admin_password)
        if not admin_token:
            print("管理员账号登录失败，请先重跑 seed_test_data.py")
            return 2
        # probe 账号的密码可能被上一次冒烟改过，两种都试
        probe_token, probe_current_password = login_any(
            client, "probe01", ["Probe@123456", "Probe@654321"]
        )
        print("令牌准备完成：" + ("学生/管理员/probe" if probe_token else "学生/管理员（probe 登录失败）"))

        # ---------------------------------------------------------- 注入令牌
        student_header = {"Authorization": f"Bearer {student_token}"}
        admin_header = {"Authorization": f"Bearer {admin_token}"}
        probe_header = (
            {"Authorization": f"Bearer {probe_token}"} if probe_token else dict(student_header)
        )

        # 需要学生身份的接口
        need_student_auth = {
            "GET  /api/auth/me  当前用户",
            "POST /api/chat  空消息 -> 422",
            "POST /api/chat  一轮问答",
            "POST /api/chat/confirm  找不到待确认动作 -> 404",
            "POST /api/chat/confirm  thread_id 不属于当前账号 -> 403",
            "POST /api/chat/confirm  兜底回放并拒绝 -> 200",
            "GET  /api/chat/history  会话历史",
            "GET  /api/chat/history  默认长会话（无数据也应为空列表）",
            "GET  /api/chat/history  limit 越界 -> 422",
            "GET  /api/chat/threads  会话列表",
            "DELETE /api/chat/thread  清空会话",
        }
        # 用 probe 账号跑的接口（改资料 / 改密码，避免动真实学生账号）
        need_probe_auth = {
            "PUT  /api/auth/profile  改资料",
            "PUT  /api/auth/profile  空姓名 -> 422",
            "PUT  /api/auth/password  原密码错误 -> 400",
        }

        for case in cases:
            authorization = (case.headers or {}).get("Authorization")
            if authorization == "__STUDENT__":
                case.headers = dict(student_header)
            elif authorization == "__ADMIN__":
                case.headers = dict(admin_header)
            elif case.name in need_probe_auth:
                case.headers = dict(probe_header)
            elif case.name in need_student_auth:
                case.headers = dict(student_header)

        # ---------------------------------------------------------- 跑用例
        results: list[Result] = [run_case(client, case) for case in cases]

        # 收尾：把 probe 账号的资料 / 密码改回原样，保证脚本能反复执行
        if probe_token:
            results.extend(_probe_aftercare(client, probe_header, probe_current_password))

    print_results(results)

    counts: dict[str, int] = {}
    for result in results:
        counts[result.outcome] = counts.get(result.outcome, 0) + 1

    print("汇总：" + "｜".join(f"{key} {counts.get(key, 0)}" for key in (OK, FAIL, SKIP, DEGRADED)))
    failures = [result for result in results if result.outcome == FAIL]
    if failures:
        print("\n失败明细：")
        for result in failures:
            print(f"  - {result.case.name}：{result.detail}")
        return 1
    return 0


def _probe_aftercare(
    client: httpx.Client, probe_header: dict[str, str], current_password: str | None
) -> list[Result]:
    """收尾：改密码往返 + 还原 probe 账号资料（保证脚本可反复执行）"""
    results: list[Result] = []

    # ① 还原资料（前面对它改过姓名 / 邮箱）
    results.append(run_case(client, Case(
        "PUT  /api/auth/profile  还原测试账号资料", "PUT", "/api/auth/profile",
        json_body={"name": "自动化测试账号", "email": "probe01@lut.edu.cn"},
        headers=dict(probe_header),
        check=make_check(message_contains="资料已更新"),
    )))

    if not current_password:
        results.append(Result(
            Case("PUT  /api/auth/password  改密码往返", "PUT", "/api/auth/password"),
            None, SKIP, "probe 账号登录失败，跳过密码往返",
        ))
        return results

    new_password = "Probe@654321" if current_password != "Probe@654321" else "Probe@123456"

    # ② 改成新密码
    results.append(run_case(client, Case(
        "PUT  /api/auth/password  改密码", "PUT", "/api/auth/password",
        json_body={"old_password": current_password, "new_password": new_password},
        headers=dict(probe_header),
        check=make_check(message_contains="密码修改成功"),
    )))

    # ③ 用新密码登录（验证真的改了）
    new_token = login(client, "probe01", new_password)
    results.append(Result(
        Case("POST /api/auth/login  改密码后用新密码登录", "POST", "/api/auth/login"),
        200 if new_token else 401,
        OK if new_token else FAIL,
        "" if new_token else "改完密码后用新密码登录失败",
    ))

    # ④ 改回原密码（用新令牌登录，旧令牌仍然有效，但用新的更贴近真实流程）
    if new_token:
        results.append(run_case(client, Case(
            "PUT  /api/auth/password  改回原密码", "PUT", "/api/auth/password",
            json_body={"old_password": new_password, "new_password": current_password},
            headers={"Authorization": f"Bearer {new_token}"},
            check=make_check(message_contains="密码修改成功"),
        )))

    return results


if __name__ == "__main__":
    raise SystemExit(main())
