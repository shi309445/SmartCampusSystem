"""前端（Web UI）接口冒烟脚本：把新增的 19 条「前端要用」的路由逐条打一遍

    .\\.venv\\Scripts\\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000
    .\\.venv\\Scripts\\python.exe scripts\\smoke_test_web_api.py

它覆盖的是这一组接口（对应前端 `frontend/src/api/*.ts`）：

| 分组 | 路由 |
|---|---|
| 实验室 | GET /api/labs、/api/labs/{id}、/api/labs/{id}/schedule、/api/labs/{id}/equipments、POST/PUT/DELETE |
| 设备   | GET /api/equipments、PUT/DELETE /api/equipments/{id}、POST /api/labs/{id}/equipments |
| 自习室 | GET /api/rooms、/api/rooms/{id}、/api/rooms/{id}/seats、POST/PUT/DELETE、POST /api/rooms/{id}/seats |
| 座位   | PUT/DELETE /api/seats/{id} |
| 我的预约 | GET /api/reservations/mine/summary、/lab/mine、/library/mine、详情、提交、取消 |
| 用户   | GET /api/users、详情、新增、修改、重置密码、删除 |

**不污染种子数据**

写操作挑的都是「种子数据没用到的时段 / 带时间戳的名字」：

    * 预约用 D102（明天 12:00-13:00，种子里没有这条）和自习室第 4 个座位；
      跑完默认会把这两条预约删掉（`--keep` 可以保留下来手工看）；
    * 实验室 / 自习室 / 用户都是临时创建，最后删掉。

前提：先跑过 `scripts/seed_test_data.py`（本脚本读 seed_manifest.json 拿 id 和密码）。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

MANIFEST_PATH = Path(__file__).resolve().parent / "seed_manifest.json"

# 北京时间；预约时间都按本地时间拼，与后端 naive datetime 一致
LOCAL_TZ = None


# ==================================================================
# 一、小工具
# ==================================================================
class Report:
    """记录每条用例的结果（和 smoke_test_api.py 一样用 OK / FAIL 两态）"""

    def __init__(self) -> None:
        self.ok = 0
        self.failed = 0
        self.failures: list[str] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.ok += 1
            print(f"  OK   {name}")
        else:
            self.failed += 1
            self.failures.append(f"{name} —— {detail}")
            print(f"  FAIL {name}   {detail}")

    def summary(self) -> int:
        total = self.ok + self.failed
        print(f"\n===== 结果：{self.ok}/{total} 通过，{self.failed} 失败 =====")
        for item in self.failures:
            print(f"  - {item}")
        return 0 if self.failed == 0 else 1


def login(client: httpx.Client, username: str, password: str) -> str:
    """登录拿 token；失败直接抛异常（后面所有用例都没意义了）"""
    res = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    body = res.json()
    if res.status_code != 200:
        raise RuntimeError(f"登录失败 {username}: {res.status_code} {body}")
    return body["data"]["token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def envelope(body: Any, status: int) -> bool:
    """统一信封：code 与 HTTP 状态码一致"""
    return (
        isinstance(body, dict)
        and int(body.get("code", -1)) == status
        and "message" in body
        and "data" in body
    )


def at(day: date, hour: int, minute: int = 0) -> str:
    """拼 ISO 时间（不带时区，后端按本地 naive 处理）"""
    return datetime.combine(day, time(hour, minute)).isoformat(timespec="seconds")


# ==================================================================
# 二、用例
# ==================================================================
def run(base_url: str, manifest: dict[str, Any], keep: bool) -> int:
    report = Report()
    passwords = manifest["passwords"]
    users = manifest["users"]
    lab_id = manifest["labs"]["物理实验室（A101）"]["id"]
    closed_lab_id = manifest["labs"]["生物标本实验室（E201）"]["id"]
    d102_id = manifest["labs"]["电子电工实验室（D102）"]["id"]
    room_id = manifest["rooms"]["三楼自主学习区"]["id"]
    seat_id = manifest["seats"]["三楼自主学习区"][3]["id"]

    tomorrow = date.today() + timedelta(days=1)
    start = at(tomorrow, 12, 0)
    end = at(tomorrow, 13, 0)

    created_lab_reservation_ids: list[int] = []
    created_lib_reservation_ids: list[int] = []
    created_user_id: int | None = None
    created_lab_id: int | None = None
    created_room_id: int | None = None

    with httpx.Client(base_url=base_url, timeout=20.0) as client:
        print("== 登录 ==")
        admin_token = login(client, "admin", passwords["admin"])
        student_token = login(client, "20210001", passwords["student"])
        other_token = login(client, "20210002", passwords["student"])
        student_id = users["20210001"]["id"]

        # ---------------------------------------------------------- 未登录
        print("\n== 未登录访问（401） ==")
        for path in ("/api/labs", "/api/rooms", "/api/reservations/lab/mine", "/api/users"):
            res = client.get(path)
            report.check(
                f"GET {path} 未登录 → 401",
                res.status_code == 401 and envelope(res.json(), 401),
                f"实际 {res.status_code} {res.text[:120]}",
            )

        # ---------------------------------------------------------- 实验室
        print("\n== 实验室 ==")
        res = client.get("/api/labs", params={"page": 1, "page_size": 5}, headers=auth(student_token))
        body = res.json()
        data = body.get("data") or {}
        report.check(
            "GET /api/labs 列表",
            res.status_code == 200
            and envelope(body, 200)
            and data.get("total", 0) >= 5
            and len(data.get("data") or []) > 0,
            f"实际 {res.status_code} {res.text[:160]}",
        )

        res = client.get(
            "/api/labs", params={"keyword": "物理", "status": 1}, headers=auth(student_token)
        )
        items = (res.json().get("data") or {}).get("data") or []
        report.check(
            "GET /api/labs 关键词 + 状态筛选",
            res.status_code == 200 and all("物理" in item["name"] for item in items),
            f"实际 {res.status_code} {res.text[:160]}",
        )

        res = client.get(f"/api/labs/{lab_id}", headers=auth(student_token))
        report.check(
            "GET /api/labs/{id} 详情",
            res.status_code == 200 and (res.json().get("data") or {}).get("id") == lab_id,
            f"实际 {res.status_code} {res.text[:160]}",
        )

        res = client.get(f"/api/labs/{lab_id}/schedule", headers=auth(student_token))
        day_data = res.json().get("data") or {}
        report.check(
            "GET /api/labs/{id}/schedule 排期 + 时段",
            res.status_code == 200
            and day_data.get("lab", {}).get("id") == lab_id
            and isinstance(day_data.get("slots"), list)
            and isinstance(day_data.get("schedule"), list),
            f"实际 {res.status_code} {res.text[:200]}",
        )
        report.check(
            "GET /api/labs/{id}/schedule 带了 date 参数回显",
            datetime.strptime(day_data["day"], "%Y-%m-%d").date() == date.today(),
            f"day={day_data.get('day')}",
        )

        res = client.get(
            f"/api/labs/{closed_lab_id}/schedule", headers=auth(student_token)
        )
        closed_data = res.json().get("data") or {}
        report.check(
            "关闭的实验室仍可看详情，且时段为空",
            res.status_code == 200 and closed_data.get("slots") == [],
            f"实际 {res.status_code} {res.text[:200]}",
        )

        res = client.get(f"/api/labs/{lab_id}/equipments", headers=auth(student_token))
        equipments = res.json().get("data") or []
        report.check(
            "GET /api/labs/{id}/equipments",
            res.status_code == 200 and len(equipments) > 0,
            f"实际 {res.status_code} {res.text[:160]}",
        )

        res = client.get(
            "/api/equipments", params={"lab_id": lab_id}, headers=auth(student_token)
        )
        equipment_total = (res.json().get("data") or {}).get("total", 0)
        report.check(
            "GET /api/equipments?lab_id=",
            res.status_code == 200 and equipment_total == len(equipments),
            f"实际 {res.status_code} total={equipment_total} 期望 {len(equipments)}",
        )

        res = client.get("/api/labs/999999", headers=auth(student_token))
        report.check(
            "GET /api/labs/999999 → 404",
            res.status_code == 404 and envelope(res.json(), 404),
            f"实际 {res.status_code} {res.text[:120]}",
        )

        # ---------------------------------------------------------- 自习室座位
        print("\n== 自习室 / 座位 ==")
        res = client.get("/api/rooms", params={"page_size": 10}, headers=auth(student_token))
        rooms_data = res.json().get("data") or {}
        report.check(
            "GET /api/rooms 列表",
            res.status_code == 200 and rooms_data.get("total", 0) >= 4,
            f"实际 {res.status_code} {res.text[:160]}",
        )

        res = client.get(f"/api/rooms/{room_id}", headers=auth(student_token))
        report.check(
            "GET /api/rooms/{id} 详情",
            res.status_code == 200 and (res.json().get("data") or {}).get("id") == room_id,
            f"实际 {res.status_code} {res.text[:160]}",
        )

        res = client.get(
            f"/api/rooms/{room_id}/seats",
            params={"start_time": start, "end_time": end},
            headers=auth(student_token),
        )
        seats_data = res.json().get("data") or {}
        report.check(
            "GET /api/rooms/{id}/seats 带时段（全部座位 + 空闲标记）",
            res.status_code == 200
            and seats_data.get("total", 0) >= 8
            and seats_data.get("available", 0) >= 6
            and all("available" in seat for seat in seats_data.get("seats") or []),
            f"实际 {res.status_code} total={seats_data.get('total')} "
            f"available={seats_data.get('available')}",
        )
        report.check(
            "明天 12:00 该自习室有座位空着",
            any(seat["id"] == seat_id and seat["available"] for seat in seats_data["seats"]),
            f"seat_id={seat_id} 不是空闲",
        )

        res = client.get(
            f"/api/rooms/{room_id}/seats",
            params={"start_time": start},
            headers=auth(student_token),
        )
        report.check(
            "只传开始时间 → 400",
            res.status_code == 400 and envelope(res.json(), 400),
            f"实际 {res.status_code} {res.text[:120]}",
        )

        res = client.get(
            f"/api/rooms/{room_id}/seats", headers=auth(student_token)
        )
        no_range = res.json().get("data") or {}
        report.check(
            "不带时段 → 按座位状态判断",
            res.status_code == 200 and no_range.get("available") == no_range.get("total"),
            f"实际 {res.status_code} available={no_range.get('available')}",
        )

        # ---------------------------------------------------------- 我的预约
        print("\n== 我的预约 ==")
        res = client.get("/api/reservations/mine/summary", headers=auth(student_token))
        summary = res.json().get("data") or {}
        report.check(
            "GET /api/reservations/mine/summary",
            res.status_code == 200
            and "lab" in summary
            and "library" in summary
            and summary["lab"]["total"] >= 0,
            f"实际 {res.status_code} {res.text[:200]}",
        )

        res = client.get(
            "/api/reservations/lab/mine", params={"page_size": 5}, headers=auth(student_token)
        )
        my_lab = res.json().get("data") or {}
        report.check(
            "GET /api/reservations/lab/mine",
            res.status_code == 200 and my_lab.get("total", 0) >= 1,
            f"实际 {res.status_code} total={my_lab.get('total')}",
        )
        report.check(
            "我的实验室预约都是自己的",
            all(item["user_id"] == student_id for item in my_lab.get("data") or []),
            "出现了别人的预约",
        )

        res = client.get(
            "/api/reservations/library/mine", headers=auth(student_token)
        )
        report.check(
            "GET /api/reservations/library/mine",
            res.status_code == 200,
            f"实际 {res.status_code} {res.text[:120]}",
        )

        res = client.get(
            "/api/reservations/lab/mine",
            params={"status": [0, 1]},
            headers=auth(student_token),
        )
        statuses = {item["status"] for item in (res.json().get("data") or {}).get("data") or []}
        report.check(
            "我的预约支持多状态筛选",
            res.status_code == 200 and statuses <= {0, 1},
            f"实际 {res.status_code} statuses={statuses}",
        )

        # ---------------------------------------------------------- 提交实验室预约
        print("\n== 提交 / 审核 / 取消（实验室） ==")
        res = client.post(
            "/api/reservations/lab",
            json={
                "lab_id": d102_id,
                "reserve_start_time": start,
                "reserve_end_time": end,
                "purpose": "冒烟测试：前端提交预约",
                "people_num": 5,
            },
            headers=auth(student_token),
        )
        created = res.json().get("data") or {}
        if res.status_code == 200:
            created_lab_reservation_ids.append(created["id"])
        report.check(
            "POST /api/reservations/lab（待审核）",
            res.status_code == 200
            and created.get("status") == 0
            and created.get("user_id") == student_id,
            f"实际 {res.status_code} {res.text[:200]}",
        )

        new_lab_reservation_id = created.get("id")

        res = client.get(
            f"/api/reservations/lab/{new_lab_reservation_id}", headers=auth(student_token)
        )
        report.check(
            "GET 自己预约的详情",
            res.status_code == 200,
            f"实际 {res.status_code} {res.text[:120]}",
        )

        res = client.get(
            f"/api/reservations/lab/{new_lab_reservation_id}", headers=auth(other_token)
        )
        report.check(
            "GET 别人的预约 → 403",
            res.status_code == 403,
            f"实际 {res.status_code} {res.text[:120]}",
        )

        res = client.put(
            f"/api/reservations/lab/{new_lab_reservation_id}/cancel",
            json={"remark": "越权取消"},
            headers=auth(other_token),
        )
        report.check(
            "PUT 取消别人的预约 → 403",
            res.status_code == 403,
            f"实际 {res.status_code} {res.text[:120]}",
        )

        res = client.post(
            "/api/reservations/lab",
            json={
                "lab_id": closed_lab_id,
                "reserve_start_time": start,
                "reserve_end_time": end,
            },
            headers=auth(student_token),
        )
        report.check(
            "预约关闭的实验室 → 409",
            res.status_code == 409 and envelope(res.json(), 409),
            f"实际 {res.status_code} {res.text[:160]}",
        )

        res = client.post(
            "/api/reservations/lab",
            json={
                "lab_id": d102_id,
                "reserve_start_time": at(tomorrow, 8, 0),
                "reserve_end_time": at(tomorrow, 9, 0),
            },
            headers=auth(student_token),
        )
        report.check(
            "预约开放时间之外 → 400",
            res.status_code == 400,
            f"实际 {res.status_code} {res.text[:160]}",
        )

        res = client.get(
            "/api/admin/reservations/lab",
            params={"status": 0, "page_size": 50},
            headers=auth(admin_token),
        )
        pending_ids = {
            item["id"] for item in (res.json().get("data") or {}).get("data") or []
        }
        report.check(
            "管理员能看到刚提交的待审核预约",
            res.status_code == 200 and new_lab_reservation_id in pending_ids,
            f"实际 {res.status_code} pending_ids={sorted(pending_ids)}",
        )

        res = client.put(
            f"/api/admin/reservations/lab/{new_lab_reservation_id}/review",
            json={"status": 1, "remark": "冒烟测试：通过"},
            headers=auth(admin_token),
        )
        report.check(
            "管理员审核通过",
            res.status_code == 200 and (res.json().get("data") or {}).get("status") == 1,
            f"实际 {res.status_code} {res.text[:160]}",
        )

        res = client.put(
            f"/api/admin/reservations/lab/{new_lab_reservation_id}/review",
            json={"status": 1},
            headers=auth(admin_token),
        )
        report.check(
            "重复审核 → 409",
            res.status_code == 409,
            f"实际 {res.status_code} {res.text[:120]}",
        )

        res = client.put(
            f"/api/reservations/lab/{new_lab_reservation_id}/cancel",
            json={"remark": "冒烟测试：取消"},
            headers=auth(student_token),
        )
        report.check(
            "学生取消自己的预约",
            res.status_code == 200 and (res.json().get("data") or {}).get("status") == 3,
            f"实际 {res.status_code} {res.text[:160]}",
        )

        # ---------------------------------------------------------- 提交座位预约
        print("\n== 提交 / 取消（自习室座位） ==")
        res = client.post(
            "/api/reservations/library",
            json={"seat_id": seat_id, "reserve_start_time": start, "reserve_end_time": end},
            headers=auth(student_token),
        )
        lib_created = res.json().get("data") or {}
        if res.status_code == 200:
            created_lib_reservation_ids.append(lib_created["id"])
        report.check(
            "POST /api/reservations/library（待审核）",
            res.status_code == 200 and lib_created.get("status") == 0,
            f"实际 {res.status_code} {res.text[:200]}",
        )

        res = client.put(
            f"/api/reservations/library/{lib_created.get('id')}/cancel",
            json={"remark": "冒烟测试：取消座位"},
            headers=auth(student_token),
        )
        report.check(
            "取消座位预约",
            res.status_code == 200 and (res.json().get("data") or {}).get("status") == 3,
            f"实际 {res.status_code} {res.text[:160]}",
        )

        res = client.get(
            "/api/admin/reservations/library", params={"page_size": 5}, headers=auth(admin_token)
        )
        report.check(
            "管理员能看到座位预约列表",
            res.status_code == 200 and (res.json().get("data") or {}).get("total", 0) >= 1,
            f"实际 {res.status_code} {res.text[:160]}",
        )

        # ---------------------------------------------------------- 用户管理
        print("\n== 用户管理（管理员） ==")
        res = client.get("/api/users", params={"keyword": "20210001"}, headers=auth(admin_token))
        found = (res.json().get("data") or {}).get("data") or []
        report.check(
            "GET /api/users 关键词搜索",
            res.status_code == 200 and len(found) == 1 and found[0]["username"] == "20210001",
            f"实际 {res.status_code} {res.text[:160]}",
        )

        res = client.get("/api/users", headers=auth(student_token))
        report.check(
            "学生访问用户管理 → 403",
            res.status_code == 403,
            f"实际 {res.status_code}",
        )

        stamp = datetime.now().strftime("%H%M%S")
        res = client.post(
            "/api/users",
            json={
                "username": f"webtest{stamp}",
                "password": "Web@Test123",
                "name": "前端冒烟账号",
                "role": "student",
            },
            headers=auth(admin_token),
        )
        new_user = res.json().get("data") or {}
        created_user_id = new_user.get("id")
        report.check(
            "POST /api/users 新增用户",
            res.status_code == 200 and new_user.get("role") == "student",
            f"实际 {res.status_code} {res.text[:160]}",
        )

        res = client.put(
            f"/api/users/{created_user_id}",
            json={"name": "前端冒烟账号（改）", "status": 0},
            headers=auth(admin_token),
        )
        checked = res.json().get("data") or {}
        report.check(
            "PUT /api/users/{id} 改资料 + 禁用",
            res.status_code == 200
            and checked.get("name") == "前端冒烟账号（改）"
            and checked.get("status") == 0,
            f"实际 {res.status_code} {res.text[:160]}",
        )

        res = client.put(
            f"/api/users/{created_user_id}/password",
            json={"new_password": "Web@Test456"},
            headers=auth(admin_token),
        )
        report.check(
            "PUT /api/users/{id}/password 重置密码",
            res.status_code == 200,
            f"实际 {res.status_code} {res.text[:160]}",
        )

        res = client.delete(f"/api/users/{created_user_id}", headers=auth(admin_token))
        report.check(
            "DELETE /api/users/{id}",
            res.status_code == 200 and created_user_id is not None,
            f"实际 {res.status_code} {res.text[:160]}",
        )
        if res.status_code == 200:
            created_user_id = None

        res = client.get(
            f"/api/admin/usage/{student_id}", headers=auth(admin_token)
        )
        report.check(
            "GET /api/admin/usage/{user_id}",
            res.status_code == 200 and "tokens" in (res.json().get("data") or {}),
            f"实际 {res.status_code} {res.text[:160]}",
        )

        # ---------------------------------------------------------- 管理员维护实验室
        print("\n== 管理员维护：实验室 / 设备 ==")
        res = client.post(
            "/api/labs",
            json={
                "name": f"前端冒烟实验室-{stamp}",
                "location": "测试楼 101",
                "maxpeople": 20,
                "open_time": "08:00:00",
                "close_time": "20:00:00",
                "description": "冒烟测试创建，跑完会自动删除",
                "status": 1,
            },
            headers=auth(admin_token),
        )
        new_lab = res.json().get("data") or {}
        created_lab_id = new_lab.get("id")
        report.check(
            "POST /api/labs 新增实验室",
            res.status_code == 200 and new_lab.get("maxpeople") == 20,
            f"实际 {res.status_code} {res.text[:200]}",
        )

        res = client.post(
            "/api/labs",
            json={"name": f"前端冒烟实验室-{stamp}", "maxpeople": 20},
            headers=auth(admin_token),
        )
        report.check(
            "同名实验室 → 409",
            res.status_code == 409,
            f"实际 {res.status_code} {res.text[:160]}",
        )

        res = client.put(
            f"/api/labs/{created_lab_id}",
            json={"location": "测试楼 202", "status": 0},
            headers=auth(admin_token),
        )
        updated = res.json().get("data") or {}
        report.check(
            "PUT /api/labs/{id} 更新实验室",
            res.status_code == 200
            and updated.get("location") == "测试楼 202"
            and updated.get("status") == 0,
            f"实际 {res.status_code} {res.text[:160]}",
        )

        res = client.post(
            f"/api/labs/{created_lab_id}/equipments",
            json={
                "equipments": [
                    {"name": "冒烟测试设备", "code": f"EQ-WEB-{stamp}", "model": "T-1"},
                ],
            },
            headers=auth(admin_token),
        )
        equipment = (res.json().get("data") or [{}])[0]
        report.check(
            "POST /api/labs/{id}/equipments 批量加设备",
            res.status_code == 200 and equipment.get("code") == f"EQ-WEB-{stamp}",
            f"实际 {res.status_code} {res.text[:200]}",
        )

        res = client.put(
            f"/api/equipments/{equipment.get('id')}",
            json={"status": 0, "description": "冒烟测试：停用"},
            headers=auth(admin_token),
        )
        report.check(
            "PUT /api/equipments/{id} 改设备",
            res.status_code == 200 and (res.json().get("data") or {}).get("status") == 0,
            f"实际 {res.status_code} {res.text[:160]}",
        )

        res = client.delete(
            f"/api/equipments/{equipment.get('id')}", headers=auth(admin_token)
        )
        report.check(
            "DELETE /api/equipments/{id}",
            res.status_code == 200,
            f"实际 {res.status_code} {res.text[:160]}",
        )

        # ---------------------------------------------------------- 管理员维护自习室
        print("\n== 管理员维护：自习室 / 座位 ==")
        res = client.post(
            "/api/rooms",
            json={
                "name": f"前端冒烟自习室-{stamp}",
                "location": "图书馆 9F",
                "capacity": 5,
                "open_time": "08:00:00",
                "close_time": "22:00:00",
                "status": 1,
            },
            headers=auth(admin_token),
        )
        new_room = res.json().get("data") or {}
        created_room_id = new_room.get("id")
        report.check(
            "POST /api/rooms 新增自习室",
            res.status_code == 200 and new_room.get("capacity") == 5,
            f"实际 {res.status_code} {res.text[:200]}",
        )

        res = client.post(
            f"/api/rooms/{created_room_id}/seats",
            json={
                "seats": [
                    {"code": f"WEB-{stamp}-A", "area": "靠窗", "has_power": True},
                    {"code": f"WEB-{stamp}-B", "area": "静音区", "has_power": False},
                ],
            },
            headers=auth(admin_token),
        )
        seats = res.json().get("data") or []
        report.check(
            "POST /api/rooms/{id}/seats 批量加座位",
            res.status_code == 200 and len(seats) == 2,
            f"实际 {res.status_code} {res.text[:200]}",
        )

        res = client.put(
            f"/api/seats/{seats[0]['id'] if seats else 0}",
            json={"has_power": False, "status": 0},
            headers=auth(admin_token),
        )
        report.check(
            "PUT /api/seats/{id} 改座位",
            res.status_code == 200 and (res.json().get("data") or {}).get("status") == 0,
            f"实际 {res.status_code} {res.text[:160]}",
        )

        res = client.delete(
            f"/api/seats/{seats[1]['id'] if len(seats) > 1 else 0}", headers=auth(admin_token)
        )
        report.check(
            "DELETE /api/seats/{id}",
            res.status_code == 200,
            f"实际 {res.status_code} {res.text[:160]}",
        )

        res = client.delete(f"/api/rooms/{created_room_id}", headers=auth(admin_token))
        report.check(
            "DELETE /api/rooms/{id}（座位级联删除）",
            res.status_code == 200,
            f"实际 {res.status_code} {res.text[:160]}",
        )
        if res.status_code == 200:
            created_room_id = None

        res = client.delete(f"/api/labs/{created_lab_id}", headers=auth(admin_token))
        report.check(
            "DELETE /api/labs/{id}",
            res.status_code == 200,
            f"实际 {res.status_code} {res.text[:160]}",
        )
        if res.status_code == 200:
            created_lab_id = None

        res = client.delete(f"/api/labs/{lab_id}", headers=auth(admin_token))
        report.check(
            "删除有预约的实验室 → 409",
            res.status_code == 409,
            f"实际 {res.status_code} {res.text[:160]}",
        )

        res = client.delete(f"/api/labs/{lab_id}", headers=auth(student_token))
        report.check(
            "学生删除实验室 → 403",
            res.status_code == 403,
            f"实际 {res.status_code}",
        )

    # ---------------------------------------------------------- 清理
    print("\n== 清理临时数据 ==")
    if keep:
        print(
            f"  --keep：保留预约 id={created_lab_reservation_ids + created_lib_reservation_ids}"
        )
    else:
        _cleanup(
            created_lab_reservation_ids,
            created_lib_reservation_ids,
            [created_user_id] if created_user_id else [],
            [created_lab_id] if created_lab_id else [],
            [created_room_id] if created_room_id else [],
        )

    return report.summary()


def _cleanup(
    lab_reservation_ids: list[int],
    lib_reservation_ids: list[int],
    user_ids: list[int],
    lab_ids: list[int],
    room_ids: list[int],
) -> None:
    """直接删掉本次冒烟造的记录（只按本次拿到的 id 删，不动其它数据）"""
    from sqlalchemy import delete

    from app.database import SessionLocal
    from app.schemas.table import User
    from app.services.laboratory.table import Equipment, Lab, LabReservation
    from app.services.library.table import Seat, SeatReservation, StudyRoom

    with SessionLocal() as db:
        removed = 0
        if lab_reservation_ids:
            removed += db.execute(
                delete(LabReservation).where(LabReservation.id.in_(lab_reservation_ids))
            ).rowcount
        if lib_reservation_ids:
            removed += db.execute(
                delete(SeatReservation).where(SeatReservation.id.in_(lib_reservation_ids))
            ).rowcount
        if lab_ids:
            removed += db.execute(
                delete(Equipment).where(Equipment.lab_id.in_(lab_ids))
            ).rowcount
            removed += db.execute(delete(Lab).where(Lab.id.in_(lab_ids))).rowcount
        if room_ids:
            removed += db.execute(delete(Seat).where(Seat.room_id.in_(room_ids))).rowcount
            removed += db.execute(delete(StudyRoom).where(StudyRoom.id.in_(room_ids))).rowcount
        if user_ids:
            removed += db.execute(delete(User).where(User.id.in_(user_ids))).rowcount
        db.commit()
    print(f"  已删除临时记录 {removed} 行")


# ==================================================================
# 三、入口
# ==================================================================
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SmartCampusSystem 前端接口冒烟")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="后端地址（默认 http://127.0.0.1:8000）",
    )
    parser.add_argument(
        "--keep", action="store_true", help="保留本次创建的预约，方便到页面上手工核对"
    )
    args = parser.parse_args(argv)

    if not MANIFEST_PATH.exists():
        print(f"缺少 {MANIFEST_PATH.name}，请先执行 scripts/seed_test_data.py")
        return 2

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    print(f"SmartCampusSystem · 前端接口冒烟（{args.base_url}）")
    return run(args.base_url, manifest, args.keep)


if __name__ == "__main__":
    raise SystemExit(main())
