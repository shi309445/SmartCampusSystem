"""app.api —— HTTP 路由（FastAPI）

按「谁能调」分组：

    登录即可
        auth.py               注册 / 登录 / 当前用户 / 改资料 / 改密码
        chat.py               发消息 / 确认写操作 / 历史 / 会话列表 / 清空会话
        lab.py                实验室列表 / 详情 / 当天排期 / 设备清单
        room.py               自习室列表 / 详情 / 座位视图
        reservation.py        我的预约 / 提交 / 取消（实验室 + 自习室）
        health.py             健康检查 / 根路径

    仅管理员（require_admin）
        lab.py 的写接口        实验室与设备增删改
        room.py 的写接口       自习室与座位增删改
        user.py               用户列表 / 新增 / 修改 / 重置密码 / 删除
        admin.py              查看学生用量
        admin_reservation.py  预约列表 / 审核 / 代取消（实验室 + 自习室）

约定：
    * 每个路由文件各自的 prefix 已经带 `/api`，这里只做汇总；
    * 路由函数尽量薄：参数校验 + 调业务/编排 + 包信封（app/common/response.py），
      业务规则留在 app/services 与 app/harness；
    * 同步阻塞调用（模型/数据库/向量库）要 `run_in_threadpool`，别堵事件循环。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    admin,
    admin_reservation,
    auth,
    chat,
    health,
    lab,
    reservation,
    room,
    user,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(chat.router)
api_router.include_router(lab.router)
api_router.include_router(lab.equipment_router)
api_router.include_router(room.router)
api_router.include_router(room.seat_router)
api_router.include_router(reservation.router)
api_router.include_router(user.router)
api_router.include_router(admin.router)
api_router.include_router(admin_reservation.router)
api_router.include_router(health.router)

__all__ = ["api_router"]
