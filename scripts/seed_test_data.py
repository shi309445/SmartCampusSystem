"""测试数据种子脚本：把「19 个 FastAPI 路由都能测」需要的数据写进库里

    .\\.venv\\Scripts\\python.exe scripts\\seed_test_data.py
    .\\.venv\\Scripts\\python.exe scripts\\seed_test_data.py --reset     # 先清理种子数据再写
    .\\.venv\\Scripts\\python.exe scripts\\seed_test_data.py --memory-only

**它写什么**

    MySQL（app/schemas/table.py + 两个业务模块的 table.py）
        users             5 个账号：管理员 / 3 个学生（含 1 个禁用）/ 1 个测试专用账号
        agent_usage       今天 + 昨天的用量（/api/admin/usage/{user_id} 才有数可看）
        labs              5 间实验室（1 间关闭），带设备
        lab_reservations  6 条预约，0/1/2/3 四种状态齐全
        library_study_rooms / library_seats / library_reservations
                          4 间自习室（1 间关闭）、24 个座位、5 条预约（四种状态齐全）

    Milvus（app/memory/store.py 的短期记忆 collection）
        3 个会话（thread）：
            u{student_id}                  正常的一问一答历史（/api/chat/history）
            u{student_id}:demo-pending     带一条**待确认**的预约写操作（/api/chat/confirm）
            u{student_id}:demo-deletable   用来测「清空会话」（/api/chat/threads + DELETE）
        注意：Milvus 没起来时这一部分会跳过并给出提示，MySQL 部分照常写入。
        历史消息的向量是零向量（embedded=false）—— 会话展示不需要向量，
        语义召回（recall）才需要，那是对话真正跑起来之后由后台线程补的。

**为什么可以反复跑**

    所有记录都靠「唯一键」定位后原地更新，不会重复插入：
        用户 -> username / 实验室 -> name / 设备 -> code / 自习室 -> name / 座位 -> code
        预约 -> reservation_no（固定写成 RESSEED0001、LIBSEED0001 …）
        用量 -> (user_id, usage_date)
        会话 -> Milvus 的 pk（{thread_id}:{seq:08d}，upsert 幂等）

**密码**

    管理员  admin    / Admin@123456
    学生    20210001 / Student@123456   （张小明，主测试账号）
            20210002 / Student@123456   （李小红）
            20210003 / Student@123456   （陈小刚，status=0，登录会被 403 拒绝）
    测试专用 probe01 / Probe@123456      （冒烟脚本用它改资料 / 改密码，不碰上面的人）

**写完之后**

    scripts/seed_manifest.json 会记下所有账号、id、会话 id，
    scripts/smoke_test_api.py 直接读它，把 19 个路由逐条打一遍。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:                 # 直接 `python scripts/xxx.py` 时也能 import app.*
    sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, select                        # noqa: E402
from sqlalchemy.orm import Session                           # noqa: E402

from app.database import SessionLocal, init_db               # noqa: E402
from app.schemas.table import (                              # noqa: E402
    ROLE_ADMIN,
    ROLE_STUDENT,
    USER_STATUS_DISABLED,
    USER_STATUS_NORMAL,
    AgentUsage,
    User,
)
from app.services.laboratory.table import (                  # noqa: E402
    RESERVATION_STATUS_APPROVED,
    RESERVATION_STATUS_CANCELLED,
    RESERVATION_STATUS_PENDING,
    RESERVATION_STATUS_REJECTED,
    Equipment,
    Lab,
    LabReservation,
)
from app.services.library.table import (                     # noqa: E402
    Seat,
    SeatReservation,
    StudyRoom,
)
from app.utils.password import hash_password                 # noqa: E402

MANIFEST_PATH = Path(__file__).resolve().parent / "seed_manifest.json"

# 预约单号前缀：--reset 靠它精确清理，不会误删真实数据
LAB_NO_PREFIX = "RESSEED"
LIB_NO_PREFIX = "LIBSEED"
SEAT_CODE_PREFIX = "SEAT-"
EQUIPMENT_CODE_PREFIX = "EQ-"

ADMIN_PASSWORD = "Admin@123456"
STUDENT_PASSWORD = "Student@123456"
PROBE_PASSWORD = "Probe@123456"

# 冒烟脚本「注册成功」用的账号前缀；--reset 会顺手清掉它们
PROBE_REGISTER_PREFIX = "probe_reg_"


# ==================================================================
# 一、数据定义（改这里就能改测试数据，不用动下面的逻辑）
# ==================================================================
ACCOUNTS: list[dict[str, Any]] = [
    {
        "username": "admin",
        "password": ADMIN_PASSWORD,
        "name": "王管理",
        "role": ROLE_ADMIN,
        "status": USER_STATUS_NORMAL,
        "email": "admin@lut.edu.cn",
        "phone": "13800000000",
        "avatar": "/static/avatars/admin.png",
    },
    {
        "username": "20210001",
        "password": STUDENT_PASSWORD,
        "name": "张小明",
        "role": ROLE_STUDENT,
        "status": USER_STATUS_NORMAL,
        "email": "20210001@lut.edu.cn",
        "phone": "13900000001",
        "avatar": "/static/avatars/20210001.png",
    },
    {
        "username": "20210002",
        "password": STUDENT_PASSWORD,
        "name": "李小红",
        "role": ROLE_STUDENT,
        "status": USER_STATUS_NORMAL,
        "email": "20210002@lut.edu.cn",
        "phone": "13900000002",
        "avatar": None,
    },
    {
        # 用来测「账号被禁用」这条分支：密码对、但 status=0
        "username": "20210003",
        "password": STUDENT_PASSWORD,
        "name": "陈小刚",
        "role": ROLE_STUDENT,
        "status": USER_STATUS_DISABLED,
        "email": None,
        "phone": None,
        "avatar": None,
    },
    {
        # 冒烟脚本专用：改资料 / 改密码都在它身上做，不动真实测试账号
        "username": "probe01",
        "password": PROBE_PASSWORD,
        "name": "自动化测试账号",
        "role": ROLE_STUDENT,
        "status": USER_STATUS_NORMAL,
        "email": "probe01@lut.edu.cn",
        "phone": "13900000009",
        "avatar": None,
    },
]

LABS: list[dict[str, Any]] = [
    {
        "name": "物理实验室（A101）",
        "location": "实验楼A座101",
        "maxpeople": 40,
        "open": (8, 0),
        "close": (22, 0),
        "description": "大学物理实验教学实验室，可完成力学、光学基础实验。",
        "img": "/static/labs/physics-a101.jpg",
        "status": 1,
        "equipments": [
            {"name": "气垫导轨实验仪", "code": "EQ-PHY-001", "model": "QG-5", "description": "验证动量守恒"},
            {"name": "分光计", "code": "EQ-PHY-002", "model": "JJY-1", "description": "测量棱镜折射率"},
            {"name": "数字示波器", "code": "EQ-PHY-003", "model": "TBS-1102", "description": "观察电信号波形"},
        ],
    },
    {
        "name": "化学基础实验室（B203）",
        "location": "实验楼B座203",
        "maxpeople": 30,
        "open": (8, 0),
        "close": (21, 0),
        "description": "无机与分析化学实验，配通风橱与应急冲淋装置。",
        "img": "/static/labs/chem-b203.jpg",
        "status": 1,
        "equipments": [
            {"name": "电子分析天平", "code": "EQ-CHM-001", "model": "FA2004", "description": "精度 0.1mg"},
            {"name": "恒温水浴锅", "code": "EQ-CHM-002", "model": "HH-6", "description": "控温范围 室温~100℃"},
        ],
    },
    {
        "name": "计算机组成原理实验室（C305）",
        "location": "信息楼C座305",
        "maxpeople": 60,
        "open": (8, 0),
        "close": (23, 0),
        "description": "组成原理与体系结构实验，60 台实验机位。",
        "img": "/static/labs/cs-c305.jpg",
        "status": 1,
        "equipments": [
            {"name": "组成原理实验箱", "code": "EQ-CS-001", "model": "TEC-8", "description": "8 位模型机实验"},
            {"name": "FPGA 开发板", "code": "EQ-CS-002", "model": "Basys3", "description": "数字逻辑与 CPU 设计"},
        ],
    },
    {
        "name": "电子电工实验室（D102）",
        "location": "实验楼D座102",
        "maxpeople": 24,
        "open": (9, 0),
        "close": (18, 0),
        "description": "电工电子基础实验，含安全用电实训区。",
        "img": None,
        "status": 1,
        "equipments": [
            {"name": "电工实验台", "code": "EQ-EE-001", "model": "DG-2", "description": "交直流电路实验"},
            {"name": "函数信号发生器", "code": "EQ-EE-002", "model": "DG1022", "description": "输出标准波形"},
        ],
    },
    {
        # 关闭状态：用来验证 search_labs / list_lab_slots 的「实验室已关闭」分支
        "name": "生物标本实验室（E201）",
        "location": "实验楼E座201",
        "maxpeople": 20,
        "open": (8, 0),
        "close": (17, 0),
        "description": "生物标本陈列与观察（本学期检修停用）。",
        "img": None,
        "status": 0,
        "equipments": [
            {"name": "生物显微镜", "code": "EQ-BIO-001", "model": "XSP-2CA", "description": "40~1600 倍"},
        ],
    },
]

STUDY_ROOMS: list[dict[str, Any]] = [
    {
        "name": "三楼自主学习区",
        "location": "图书馆3F东侧",
        "capacity": 120,
        "open": (7, 0),
        "close": (22, 30),
        "description": "开放式自习区，可小声讨论，靠窗座位采光好。",
        "img": "/static/rooms/3f-east.jpg",
        "status": 1,
        "seats": [
            {"area": "靠窗", "has_power": True},
            {"area": "靠窗", "has_power": True},
            {"area": "静音区", "has_power": True},
            {"area": "静音区", "has_power": False},
            {"area": "中间区", "has_power": True},
            {"area": "中间区", "has_power": False},
            {"area": "靠窗", "has_power": False},
            {"area": "中间区", "has_power": True},
        ],
    },
    {
        "name": "四楼静音自习室",
        "location": "图书馆4F北侧",
        "capacity": 80,
        "open": (8, 0),
        "close": (22, 0),
        "description": "全程静音，禁止交谈与接打电话。",
        "img": None,
        "status": 1,
        "seats": [
            {"area": "静音区", "has_power": True},
            {"area": "静音区", "has_power": True},
            {"area": "静音区", "has_power": False},
            {"area": "靠窗", "has_power": True},
            {"area": "靠窗", "has_power": False},
            {"area": "中间区", "has_power": True},
        ],
    },
    {
        "name": "二楼期刊阅览室",
        "location": "图书馆2F中庭",
        "capacity": 60,
        "open": (8, 0),
        "close": (21, 30),
        "description": "可查阅纸质期刊，允许携带笔记本电脑。",
        "img": None,
        "status": 1,
        "seats": [
            {"area": "阅览区", "has_power": True},
            {"area": "阅览区", "has_power": True},
            {"area": "阅览区", "has_power": False},
            {"area": "靠窗", "has_power": True},
            {"area": "靠窗", "has_power": True},
            {"area": "中间区", "has_power": False},
        ],
    },
    {
        # 关闭状态：验证 find_available_seats 的「自习室已关闭」分支
        "name": "五楼考研专区",
        "location": "图书馆5F",
        "capacity": 50,
        "open": (8, 0),
        "close": (23, 0),
        "description": "考研专用自习区（暑期闭馆维护）。",
        "img": None,
        "status": 0,
        "seats": [
            {"area": "考研区", "has_power": True},
            {"area": "考研区", "has_power": True},
        ],
    },
]

# 实验室预约：status 0-待审核 1-预约成功 2-预约失败 3-已取消
LAB_RESERVATIONS: list[dict[str, Any]] = [
    {
        "no": f"{LAB_NO_PREFIX}0001", "lab": "物理实验室（A101）", "user": "20210001",
        "day": 1, "start": (14, 0), "end": (16, 0), "status": RESERVATION_STATUS_PENDING,
        "people_num": 8, "purpose": "大学物理实验：测量重力加速度",
    },
    {
        "no": f"{LAB_NO_PREFIX}0002", "lab": "物理实验室（A101）", "user": "20210002",
        "day": 1, "start": (9, 0), "end": (11, 0), "status": RESERVATION_STATUS_APPROVED,
        "people_num": 6, "purpose": "光学实验：分光计测棱镜折射率",
        "remark": "已核对设备清单，准予使用",
    },
    {
        "no": f"{LAB_NO_PREFIX}0003", "lab": "化学基础实验室（B203）", "user": "20210001",
        "day": 2, "start": (10, 0), "end": (12, 0), "status": RESERVATION_STATUS_APPROVED,
        "people_num": 4, "purpose": "分析化学滴定实验",
        "remark": "注意佩戴护目镜",
    },
    {
        "no": f"{LAB_NO_PREFIX}0004", "lab": "计算机组成原理实验室（C305）", "user": "20210002",
        "day": 1, "start": (15, 0), "end": (17, 0), "status": RESERVATION_STATUS_REJECTED,
        "people_num": 20, "purpose": "课程设计联调",
        "remark": "该时段已安排课程实验，请改约其它时段",
    },
    {
        "no": f"{LAB_NO_PREFIX}0005", "lab": "计算机组成原理实验室（C305）", "user": "20210001",
        "day": 3, "start": (13, 0), "end": (15, 0), "status": RESERVATION_STATUS_CANCELLED,
        "people_num": 5, "purpose": "FPGA 流水线 CPU 调试",
        "remark": "学生本人取消：课程时间冲突",
    },
    {
        "no": f"{LAB_NO_PREFIX}0006", "lab": "电子电工实验室（D102）", "user": "20210002",
        "day": 4, "start": (9, 0), "end": (11, 0), "status": RESERVATION_STATUS_APPROVED,
        "people_num": 6, "purpose": "电工实训：三相电路",
        "remark": "已安排指导教师",
    },
]

# 自习室座位预约：day 偏移不超过 3 天（业务上限 MAX_ADVANCE_DAYS=3）
LIB_RESERVATIONS: list[dict[str, Any]] = [
    {
        "no": f"{LIB_NO_PREFIX}0001", "room": "三楼自主学习区", "seat_index": 0, "user": "20210001",
        "day": 1, "start": (14, 0), "end": (16, 0), "status": RESERVATION_STATUS_APPROVED,
    },
    {
        "no": f"{LIB_NO_PREFIX}0002", "room": "三楼自主学习区", "seat_index": 1, "user": "20210002",
        "day": 1, "start": (14, 0), "end": (16, 0), "status": RESERVATION_STATUS_PENDING,
    },
    {
        "no": f"{LIB_NO_PREFIX}0003", "room": "四楼静音自习室", "seat_index": 0, "user": "20210002",
        "day": 2, "start": (9, 0), "end": (11, 0), "status": RESERVATION_STATUS_REJECTED,
        "remark": "所选座位在维护，建议改选其它座位",
    },
    {
        "no": f"{LIB_NO_PREFIX}0004", "room": "二楼期刊阅览室", "seat_index": 2, "user": "20210001",
        "day": 3, "start": (19, 0), "end": (21, 0), "status": RESERVATION_STATUS_CANCELLED,
        "remark": "学生本人取消",
    },
    {
        "no": f"{LIB_NO_PREFIX}0005", "room": "三楼自主学习区", "seat_index": 2, "user": "20210001",
        "day": 2, "start": (10, 0), "end": (12, 0), "status": RESERVATION_STATUS_APPROVED,
    },
]

# (user_id, 日期偏移, tokens, calls, steps)
USAGE_ROWS: list[tuple[str, int, int, int, int]] = [
    ("20210001", 0, 12345, 34, 12),
    ("20210001", -1, 8000, 21, 6),
    ("20210002", 0, 120000, 180, 40),
    ("probe01", 0, 512, 3, 1),
]

# 会话内容：给 /api/chat/history 展示用
DEMO_HISTORY: list[tuple[str, str, dict[str, Any] | None]] = [
    ("human", "国家励志奖学金需要满足什么条件？", None),
    ("ai", "", {
        "tool_calls": [{
            "name": "retrieval_knowledge_base",
            "args": {"query": "国家励志奖学金 申请条件"},
            "id": "call_seed_rag_0001",
            "type": "tool_call",
        }],
    }),
    ("tool", "[1] 《兰州理工大学国家励志奖学金实施办法》第二章 · 申请条件\n"
             "二年级以上（含二年级）全日制本科在校生，上一学年综合测评成绩排名专业前 30%，"
             "且家庭经济困难、生活俭朴。", {
        "tool_call_id": "call_seed_rag_0001",
        "name": "retrieval_knowledge_base",
        "status": "success",
    }),
    ("ai", "依据 [1]《兰州理工大学国家励志奖学金实施办法》，需要同时满足：\n"
           "1. 二年级以上（含二年级）全日制本科在校生；\n"
           "2. 上一学年综合测评成绩排名专业前 30%；\n"
           "3. 家庭经济困难、生活俭朴。\n"
           "每年 9 月由学院组织申报，具体材料清单以学院通知为准。", None),
    ("human", "那助学贷款怎么申请？", None),
    ("ai", "助学贷款分生源地信用助学贷款和校园地国家助学贷款两种，新生一般走生源地贷款："
           "凭录取通知书到户籍所在地县级学生资助管理中心办理。建议先查看新生「绿色通道」实施办法。", None),
]

DELETABLE_HISTORY: list[tuple[str, str, dict[str, Any] | None]] = [
    ("human", "这是一条用来测试「清空会话」的临时消息", None),
    ("ai", "好的，这个会话可以整段删除，不影响其它测试数据。", None),
]


# ==================================================================
# 二、小工具
# ==================================================================
def _at(day_offset: int, hour: int, minute: int = 0) -> datetime:
    """今天 + day_offset 天的某个时刻（本地时间，不带时区）"""
    return datetime.combine(date.today() + timedelta(days=day_offset), time(hour, minute))


def _upsert(db: Session, model: type, lookup: dict[str, Any], values: dict[str, Any]) -> Any:
    """按 lookup 定位，存在就更新 values，不存在就插入；返回 ORM 对象"""
    obj = db.scalars(select(model).filter_by(**lookup)).first()
    if obj is None:
        obj = model(**{**lookup, **values})
        db.add(obj)
    else:
        for key, value in values.items():
            setattr(obj, key, value)
    db.flush()
    return obj


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


# ==================================================================
# 三、MySQL 种子数据
# ==================================================================
def seed_users(db: Session) -> dict[str, User]:
    _section("账号")
    users: dict[str, User] = {}
    for account in ACCOUNTS:
        values = {
            "password": hash_password(account["password"]),   # 每次都重置成文档里的密码
            "name": account["name"],
            "role": account["role"],
            "status": account["status"],
            "email": account["email"],
            "phone": account["phone"],
            "avatar": account["avatar"],
        }
        user = _upsert(db, User, {"username": account["username"]}, values)
        users[account["username"]] = user
        state = "正常" if account["status"] == USER_STATUS_NORMAL else "禁用"
        print(f"  {account['username']:<12} {account['name']:<6} {account['role']:<8} "
              f"{state}  id={user.id}")
    db.commit()
    return users


def seed_usage(db: Session, users: dict[str, User]) -> list[AgentUsage]:
    """写入「今天 / 昨天」的用量

    用量是 (user_id, usage_date) 唯一，所以每跑一次种子的同一天内是覆盖写；
    但**隔天再跑**会留下前几天的旧行（每天新增一条）。那些旧行不影响接口
    （`/api/admin/usage` 只查今天），却会让 `--verify` 的行数越跑越多。
    所以这里顺手把**种子账号的非目标日期**清掉，保证重复执行后行数稳定。
    """
    _section("Agent 用量（/api/admin/usage/{user_id}）")
    rows: list[AgentUsage] = []
    target_dates: dict[int, set[date]] = {}

    for username, day_offset, tokens, calls, steps in USAGE_ROWS:
        user = users[username]
        usage_date = date.today() + timedelta(days=day_offset)
        target_dates.setdefault(user.id, set()).add(usage_date)
        row = _upsert(
            db,
            AgentUsage,
            {"user_id": user.id, "usage_date": usage_date},
            {"tokens": tokens, "calls": calls, "steps": steps},
        )
        rows.append(row)
        print(f"  user_id={user.id:<4} {username:<10} {usage_date}  "
              f"tokens={tokens:<7} calls={calls:<4} steps={steps}")

    # 清掉种子账号的「非目标日期」历史用量（见函数文档）
    stale = 0
    for user_id, dates in target_dates.items():
        stale += db.execute(
            delete(AgentUsage).where(
                AgentUsage.user_id == user_id,
                AgentUsage.usage_date.notin_(dates),
            )
        ).rowcount
    if stale:
        print(f"  清理历史用量行：{stale} 条")

    db.commit()
    return rows


def seed_labs(db: Session) -> dict[str, Lab]:
    _section("实验室与设备")
    labs: dict[str, Lab] = {}
    for spec in LABS:
        lab = _upsert(
            db,
            Lab,
            {"name": spec["name"]},
            {
                "location": spec["location"],
                "maxpeople": spec["maxpeople"],
                "open_time": time(*spec["open"]),
                "close_time": time(*spec["close"]),
                "description": spec["description"],
                "img": spec["img"],
                "status": spec["status"],
            },
        )
        labs[spec["name"]] = lab

        for equipment in spec["equipments"]:
            _upsert(
                db,
                Equipment,
                {"code": equipment["code"]},
                {
                    "name": equipment["name"],
                    "model": equipment["model"],
                    "description": equipment["description"],
                    "status": 1,
                    "lab_id": lab.id,
                },
            )
        state = "开放" if spec["status"] == 1 else "关闭"
        print(f"  lab_id={lab.id:<3} {spec['name']:<26} {state}  "
              f"容量 {spec['maxpeople']:<3} 设备 {len(spec['equipments'])} 台")
    db.commit()
    return labs


def seed_lab_reservations(
    db: Session, users: dict[str, User], labs: dict[str, Lab]
) -> dict[str, LabReservation]:
    _section("实验室预约（四种状态）")
    admin = users["admin"]
    reservations: dict[str, LabReservation] = {}

    for spec in LAB_RESERVATIONS:
        lab = labs[spec["lab"]]
        user = users[spec["user"]]
        start = _at(spec["day"], *spec["start"])
        end = _at(spec["day"], *spec["end"])
        handled = spec["status"] in (
            RESERVATION_STATUS_APPROVED,
            RESERVATION_STATUS_REJECTED,
        )
        row = _upsert(
            db,
            LabReservation,
            {"reservation_no": spec["no"]},
            {
                "lab_id": lab.id,
                "lab_name": lab.name,
                "user_id": user.id,
                "user_name": user.name,
                "purpose": spec["purpose"],
                "people_num": spec["people_num"],
                "reserve_start_time": start,
                "reserve_end_time": end,
                "status": spec["status"],
                "remark": spec.get("remark"),
                "handler_id": admin.id if handled else None,
                "handler_name": admin.name if handled else None,
                "handle_time": _at(spec["day"], spec["end"][0], spec["end"][1]) - timedelta(hours=1)
                if handled else None,
            },
        )
        reservations[spec["no"]] = row
        print(f"  {spec['no']}  id={row.id:<3} {lab.name:<26} {user.name:<4} "
              f"{start:%m-%d %H:%M}~{end:%H:%M}  状态={spec['status']}")
    db.commit()
    return reservations


def seed_rooms(db: Session) -> tuple[dict[str, StudyRoom], dict[str, list[Seat]]]:
    _section("自习室与座位")
    rooms: dict[str, StudyRoom] = {}
    seats: dict[str, list[Seat]] = {}

    for spec in STUDY_ROOMS:
        room = _upsert(
            db,
            StudyRoom,
            {"name": spec["name"]},
            {
                "location": spec["location"],
                "capacity": spec["capacity"],
                "open_time": time(*spec["open"]),
                "close_time": time(*spec["close"]),
                "description": spec["description"],
                "img": spec["img"],
                "status": spec["status"],
            },
        )
        rooms[spec["name"]] = room

        room_seats: list[Seat] = []
        for index, seat_spec in enumerate(spec["seats"], start=1):
            code = f"{SEAT_CODE_PREFIX}{room.id:02d}-{index:03d}"
            seat = _upsert(
                db,
                Seat,
                {"code": code},
                {
                    "area": seat_spec["area"],
                    "has_power": seat_spec["has_power"],
                    "description": f"{room.name}{seat_spec['area']}座位",
                    "status": 1,
                    "room_id": room.id,
                },
            )
            room_seats.append(seat)
        seats[spec["name"]] = room_seats

        state = "开放" if spec["status"] == 1 else "关闭"
        print(f"  room_id={room.id:<3} {spec['name']:<16} {state}  "
              f"上限 {spec['capacity']:<4} 座位 {len(room_seats)} 个")
    db.commit()
    return rooms, seats


def seed_lib_reservations(
    db: Session,
    users: dict[str, User],
    rooms: dict[str, StudyRoom],
    seats: dict[str, list[Seat]],
) -> dict[str, SeatReservation]:
    _section("自习室座位预约（四种状态）")
    reservations: dict[str, SeatReservation] = {}

    for spec in LIB_RESERVATIONS:
        room = rooms[spec["room"]]
        seat = seats[spec["room"]][spec["seat_index"]]
        user = users[spec["user"]]
        start = _at(spec["day"], *spec["start"])
        end = _at(spec["day"], *spec["end"])
        row = _upsert(
            db,
            SeatReservation,
            {"reservation_no": spec["no"]},
            {
                "room_id": room.id,
                "room_name": room.name,
                "seat_id": seat.id,
                "seat_code": seat.code,
                "user_id": user.id,
                "user_name": user.name,
                "reserve_start_time": start,
                "reserve_end_time": end,
                "status": spec["status"],
                "remark": spec.get("remark"),
                "handler_id": None,
                "handler_name": None,
                "handle_time": None,
            },
        )
        reservations[spec["no"]] = row
        print(f"  {spec['no']}  id={row.id:<3} {room.name:<14} {seat.code:<12} "
              f"{user.name:<4} 状态={spec['status']}")
    db.commit()
    return reservations


# ==================================================================
# 四、Milvus 短期记忆（会话历史）
# ==================================================================
def _message_row(
    thread_id: str,
    user_id: int,
    seq: int,
    role: str,
    content: str,
    payload: dict[str, Any] | None = None,
    *,
    created_at_ms: int,
    dim: int,
) -> dict[str, Any]:
    """拼一条记忆行（字段与 app/memory/store.py 的 collection schema 一一对应）"""
    return {
        "pk": f"{thread_id}:{seq:08d}",
        "thread_id": thread_id,
        "user_id": int(user_id),
        "seq": int(seq),
        "role": role,
        "content": content or "",
        "payload": payload or {},
        "created_at": int(created_at_ms),
        "embedded": False,          # 零向量：会话展示不需要向量
        "embedding": [0.0] * int(dim),
    }


def seed_memory(
    users: dict[str, User],
    labs: dict[str, Lab],
    rooms: dict[str, StudyRoom],
    seats: dict[str, list[Seat]],
) -> dict[str, Any]:
    """把 3 个会话写进 Milvus；Milvus 不可用就跳过（不影响 MySQL 部分）"""
    _section("会话历史（Milvus 短期记忆）")

    from app.memory.store import VECTOR_DIM, get_store

    store = get_store()
    if not store.ensure():
        print("  Milvus 不可用，跳过会话历史写入。")
        print("  → /api/chat/history、/api/chat/threads 仍可调用，只是会返回空列表。")
        print("  → 想连上：启动 Milvus（默认 http://localhost:19530）后重跑本脚本，"
              "或用 --memory-only。")
        return {"seeded": False, "threads": {}, "reason": "milvus_unavailable"}

    student = users["20210001"]
    uid = int(student.id)
    main_thread = f"u{uid}"
    pending_thread = f"u{uid}:demo-pending"
    deletable_thread = f"u{uid}:demo-deletable"

    rows: list[dict[str, Any]] = []

    # ① 正常历史会话
    base_ms = int((datetime.now() - timedelta(hours=2)).timestamp() * 1000)
    for index, (role, content, payload) in enumerate(DEMO_HISTORY, start=1):
        rows.append(_message_row(
            main_thread, uid, index, role, content, payload,
            created_at_ms=base_ms + index * 20_000, dim=VECTOR_DIM,
        ))

    # ② 带「待确认」写操作的会话：/api/chat/confirm 可直接拿这个 tool_call_id 试
    lab = labs["物理实验室（A101）"]
    room = rooms["三楼自主学习区"]
    seat = seats["三楼自主学习区"][2]
    start = _at(1, 14, 0)
    end = _at(1, 16, 0)
    pending_tool_call_id = "call_seed_pending_0001"
    pending_args = {
        "affair_name": "lab",
        "lab_id": lab.id,
        "reserve_start_time": start.strftime("%Y-%m-%d %H:%M"),
        "reserve_end_time": end.strftime("%Y-%m-%d %H:%M"),
        "reserve_date": start.strftime("%Y-%m-%d"),
        "people_num": 6,
        "purpose": "物理实验（待确认）",
    }
    pending_summary = f"预约实验室（lab_id={lab.id}），时间 {start:%m-%d %H:%M} ~ {end:%H:%M}"
    pending_payload = {
        "requires_confirmation": True,
        "tool_call_id": pending_tool_call_id,
        "tool_name": "make_reservation",
        "args": pending_args,
        "summary": pending_summary,
    }

    base_ms += 600_000
    rows.extend([
        _message_row(
            pending_thread, uid, 1, "human", "帮我约明天下午两点的物理实验室",
            created_at_ms=base_ms, dim=VECTOR_DIM,
        ),
        _message_row(
            pending_thread, uid, 2, "ai", "",
            {
                "tool_calls": [{
                    "name": "make_reservation",
                    "args": pending_args,
                    "id": pending_tool_call_id,
                    "type": "tool_call",
                }],
            },
            created_at_ms=base_ms + 1_000, dim=VECTOR_DIM,
        ),
        _message_row(
            pending_thread, uid, 3, "tool",
            f"【待确认】{pending_summary}。学生确认后才会真正提交。",
            {
                "tool_call_id": pending_tool_call_id,
                "name": "make_reservation",
                "status": "success",
                "pending_action": pending_payload,
            },
            created_at_ms=base_ms + 2_000, dim=VECTOR_DIM,
        ),
    ])

    # ③ 可整段删除的会话：验证 DELETE /api/chat/thread
    base_ms += 600_000
    for index, (role, content, payload) in enumerate(DELETABLE_HISTORY, start=1):
        rows.append(_message_row(
            deletable_thread, uid, index, role, content, payload,
            created_at_ms=base_ms + index * 10_000, dim=VECTOR_DIM,
        ))

    written = store.upsert(rows)
    print(f"  写入 {written} 条消息")
    for thread_id in (main_thread, pending_thread, deletable_thread):
        count = store.count(thread_id)
        print(f"  thread={thread_id:<28} {count} 条")

    return {
        "seeded": True,
        "threads": {
            "main": main_thread,
            "pending": pending_thread,
            "deletable": deletable_thread,
        },
        "pending_tool_call_id": pending_tool_call_id,
        "pending_summary": pending_summary,
        "pending_args": pending_args,
        # 冒烟脚本用这个会话做「无匹配 tool_call」的兜底回放测试，不会破坏上面的待确认动作
        "probe_thread": f"u{uid}:probe-confirm",
        "library_seat_id": seat.id,
        "library_room_id": room.id,
    }


# ==================================================================
# 五、清理
# ==================================================================
def reset_seed_data(db: Session) -> None:
    """只删本脚本写的数据（按固定标记定位），不动其它任何行"""
    _section("清理种子数据")

    usernames = [account["username"] for account in ACCOUNTS]
    seed_users = db.scalars(select(User).where(User.username.in_(usernames))).all()
    seed_user_ids = [user.id for user in seed_users]

    deleted = db.execute(
        delete(LabReservation).where(LabReservation.reservation_no.like(f"{LAB_NO_PREFIX}%"))
    ).rowcount
    deleted += db.execute(
        delete(SeatReservation).where(SeatReservation.reservation_no.like(f"{LIB_NO_PREFIX}%"))
    ).rowcount
    print(f"  预约：{deleted} 条")

    deleted = db.execute(
        delete(Equipment).where(Equipment.code.like(f"{EQUIPMENT_CODE_PREFIX}%"))
    ).rowcount
    print(f"  设备：{deleted} 条")

    deleted = db.execute(
        delete(Seat).where(Seat.code.like(f"{SEAT_CODE_PREFIX}%"))
    ).rowcount
    print(f"  座位：{deleted} 条")

    lab_names = [spec["name"] for spec in LABS]
    deleted = db.execute(delete(Lab).where(Lab.name.in_(lab_names))).rowcount
    print(f"  实验室：{deleted} 间")

    room_names = [spec["name"] for spec in STUDY_ROOMS]
    deleted = db.execute(delete(StudyRoom).where(StudyRoom.name.in_(room_names))).rowcount
    print(f"  自习室：{deleted} 间")

    if seed_user_ids:
        deleted = db.execute(
            delete(AgentUsage).where(AgentUsage.user_id.in_(seed_user_ids))
        ).rowcount
        print(f"  Agent 用量：{deleted} 行")

    # 孤儿用量行：用户已经被删（历史 reset / 手工删号），用量行还在。
    # `--verify` 会把它们算进行数，看起来像"种子数据没清干净"，所以一并清掉。
    orphan = db.execute(
        delete(AgentUsage).where(AgentUsage.user_id.notin_(select(User.id)))
    ).rowcount
    if orphan:
        print(f"  孤儿用量行（用户已不存在）：{orphan} 条")

    # 冒烟脚本注册出来的临时账号
    deleted = db.execute(
        delete(User).where(User.username.like(f"{PROBE_REGISTER_PREFIX}%"))
    ).rowcount
    print(f"  冒烟临时账号：{deleted} 个")

    deleted = db.execute(delete(User).where(User.username.in_(usernames))).rowcount
    print(f"  种子账号：{deleted} 个")
    db.commit()

    _reset_memory()


def _reset_memory() -> None:
    """删掉本脚本写的三个会话（Milvus 不可用时静默跳过）"""
    from app.memory.store import get_store

    store = get_store()
    if not store.ensure():
        print("  会话历史：Milvus 不可用，跳过")
        return

    dropped = 0
    for thread_id in _seed_thread_ids():
        dropped += store.drop_thread(thread_id)
    print(f"  会话历史：{dropped} 条")


def _seed_thread_ids() -> list[str]:
    """种子会话 id 列表（不依赖数据库里的 user_id，用 manifest 兜底）"""
    threads = ["u{}", "u{}:demo-pending", "u{}:demo-deletable", "u{}:probe-confirm"]
    user_id = None
    if MANIFEST_PATH.exists():
        try:
            manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            user_id = manifest.get("users", {}).get("20210001", {}).get("id")
        except (OSError, json.JSONDecodeError):
            user_id = None

    if user_id is None:
        from app.database import SessionLocal
        from app.schemas.table import User as UserModel

        with SessionLocal() as db:
            user = db.scalars(select(UserModel).filter_by(username="20210001")).first()
            user_id = user.id if user else None

    if user_id is None:
        return []
    return [pattern.format(user_id) for pattern in threads]


def purge_business_data(db: Session) -> None:
    """清空全部业务表（危险：用于「要一个彻底干净的环境」时）"""
    _section("清空全部业务数据")
    for model in (
        LabReservation,
        SeatReservation,
        Equipment,
        Seat,
        Lab,
        StudyRoom,
        AgentUsage,
        User,
    ):
        deleted = db.execute(delete(model)).rowcount
        print(f"  {model.__tablename__}: {deleted} 行")
    db.commit()


TABLES: tuple[type, ...] = (
    User,
    AgentUsage,
    Lab,
    Equipment,
    LabReservation,
    StudyRoom,
    Seat,
    SeatReservation,
)


def verify(db: Session) -> None:
    """打印每张业务表的行数（确认种子数据「有没有、有没有重复」）"""
    from sqlalchemy import func

    _section("数据库现状")
    for model in TABLES:
        count = int(db.scalar(select(func.count()).select_from(model)) or 0)
        print(f"  {model.__tablename__:<22} {count:>4} 行")

    registered = int(
        db.scalar(
            select(func.count()).select_from(User).where(
                User.username.like(f"{PROBE_REGISTER_PREFIX}%")
            )
        ) or 0
    )
    print(f"  （其中冒烟临时账号 {registered} 个，--reset 会清掉）")


# ==================================================================
# 六、写 manifest（冒烟脚本读它）
# ==================================================================
def write_manifest(payload: dict[str, Any]) -> None:
    MANIFEST_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\n清单已写入：{MANIFEST_PATH.relative_to(ROOT)}")


def build_manifest(
    users: dict[str, User],
    labs: dict[str, Lab],
    lab_reservations: dict[str, LabReservation],
    rooms: dict[str, StudyRoom],
    seats: dict[str, list[Seat]],
    lib_reservations: dict[str, SeatReservation],
    memory: dict[str, Any],
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "base_url": "http://127.0.0.1:8000",
        "passwords": {
            "admin": ADMIN_PASSWORD,
            "student": STUDENT_PASSWORD,
            "probe": PROBE_PASSWORD,
        },
        "users": {
            username: {
                "id": user.id,
                "name": user.name,
                "role": user.role,
                "status": user.status,
                "password": next(
                    account["password"] for account in ACCOUNTS
                    if account["username"] == username
                ),
            }
            for username, user in users.items()
        },
        "usage": {
            "student_today": {"user_id": users["20210001"].id, "tokens": 12345, "calls": 34},
            "student_near_limit": {"user_id": users["20210002"].id, "tokens": 120000, "calls": 180},
        },
        "labs": {name: {"id": lab.id, "status": lab.status} for name, lab in labs.items()},
        "lab_reservations": {
            no: {
                "id": row.id,
                "lab_id": row.lab_id,
                "user_id": row.user_id,
                "status": row.status,
                "start": row.reserve_start_time,
                "end": row.reserve_end_time,
            }
            for no, row in lab_reservations.items()
        },
        "rooms": {name: {"id": room.id, "status": room.status} for name, room in rooms.items()},
        "seats": {
            name: [{"id": seat.id, "code": seat.code} for seat in seat_list]
            for name, seat_list in seats.items()
        },
        "lib_reservations": {
            no: {
                "id": row.id,
                "room_id": row.room_id,
                "seat_id": row.seat_id,
                "user_id": row.user_id,
                "status": row.status,
                "start": row.reserve_start_time,
                "end": row.reserve_end_time,
            }
            for no, row in lib_reservations.items()
        },
        "memory": memory,
    }


# ==================================================================
# 七、入口
# ==================================================================
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="写入 SmartCampusSystem 的测试数据（可反复执行）"
    )
    parser.add_argument("--reset", action="store_true", help="先清理种子数据，再重新写入")
    parser.add_argument("--purge", action="store_true", help="清空全部业务表（危险，需配 --yes）")
    parser.add_argument("--yes", action="store_true", help="确认执行 --purge")
    parser.add_argument("--skip-memory", action="store_true", help="不写 Milvus 会话历史")
    parser.add_argument("--memory-only", action="store_true", help="只写 Milvus 会话历史")
    parser.add_argument("--verify", action="store_true", help="只打印各表行数，不写数据")
    parser.add_argument(
        "--cleanup-probe", action="store_true",
        help="只删掉冒烟脚本注册的 probe_reg_* 临时账号（不动其它数据）",
    )
    args = parser.parse_args(argv)

    if args.purge and not args.yes:
        print("--purge 会清空全部业务表，请追加 --yes 确认。")
        return 2

    print("SmartCampusSystem · 测试数据种子")
    init_db()

    if args.cleanup_probe:
        with SessionLocal() as db:
            deleted = db.execute(
                delete(User).where(User.username.like(f"{PROBE_REGISTER_PREFIX}%"))
            ).rowcount
            db.commit()
        print(f"已删除冒烟临时账号 {deleted} 个")
        return 0

    if args.verify:
        with SessionLocal() as db:
            verify(db)
        return 0

    memory: dict[str, Any] = {"seeded": False, "threads": {}}
    with SessionLocal() as db:
        if args.purge:
            purge_business_data(db)
        if args.reset:
            reset_seed_data(db)

        if args.memory_only:
            # 只写 Milvus 时需要先拿到「已有的」账号与对象，不重新写 MySQL
            users = _load_existing_users(db)
            if not users:
                print("库里还没有种子账号，先跑一次不带 --memory-only 的本脚本。")
                return 1
            labs = _load_existing_by_name(db, Lab, "物理实验室（A101）")
            rooms = _load_existing_by_name(db, StudyRoom, "三楼自主学习区")
            seats = {"三楼自主学习区": _load_seats(db, rooms)}
            if not args.skip_memory:
                memory = seed_memory(users, labs, rooms, seats)
            manifest = build_manifest(users, labs, {}, rooms, seats, {}, memory)
            write_manifest(manifest)
            return 0

        users = seed_users(db)
        seed_usage(db, users)
        labs = seed_labs(db)
        lab_reservations = seed_lab_reservations(db, users, labs)
        rooms, seats = seed_rooms(db)
        lib_reservations = seed_lib_reservations(db, users, rooms, seats)

        if not args.skip_memory:
            memory = seed_memory(users, labs, rooms, seats)

        manifest = build_manifest(
            users, labs, lab_reservations, rooms, seats, lib_reservations, memory
        )

    write_manifest(manifest)
    _print_summary(manifest)
    return 0


def _load_existing_users(db: Session) -> dict[str, User]:
    usernames = [account["username"] for account in ACCOUNTS]
    rows = db.scalars(select(User).where(User.username.in_(usernames))).all()
    return {row.username: row for row in rows}


def _load_existing_by_name(db: Session, model: type, name: str) -> dict[str, Any]:
    row = db.scalars(select(model).filter_by(name=name)).first()
    return {name: row} if row is not None else {}


def _load_seats(db: Session, rooms: dict[str, Any]) -> list[Seat]:
    room = next(iter(rooms.values()), None)
    if room is None:
        return []
    return list(db.scalars(select(Seat).filter_by(room_id=room.id).order_by(Seat.id)).all())


def _print_summary(manifest: dict[str, Any]) -> None:
    users = manifest["users"]
    print("\n=== 可以直接拿去测的账号 ===")
    for username, info in users.items():
        state = "正常" if info["status"] == 1 else "禁用"
        print(f"  {username:<10} / {info['password']:<16} {info['role']:<8} "
              f"{info['name']:<6} id={info['id']:<3} {state}")

    memory = manifest.get("memory", {})
    print("\n=== 会话历史 ===")
    if memory.get("seeded"):
        for label, thread_id in memory["threads"].items():
            print(f"  {label:<10} {thread_id}")
        print(f"  待确认动作 tool_call_id = {memory['pending_tool_call_id']}")
    else:
        print("  未写入（Milvus 不可用）")

    print("\n=== 下一步 ===")
    print("  1) 启动服务：.\\\\.venv\\\\Scripts\\\\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000")
    print("  2) 逐条验证：.\\\\.venv\\\\Scripts\\\\python.exe scripts\\\\smoke_test_api.py")


if __name__ == "__main__":
    raise SystemExit(main())
