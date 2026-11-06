"""FastAPI 应用入口

启动：

    .\\.venv\\Scripts\\python.exe -m app.main
    # 或
    .\\.venv\\Scripts\\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000

文档：http://127.0.0.1:8000/docs

**启动顺序（lifespan）**

    1. setup_logging()   控制台 + logs/*.jsonl（带 trace_id）
    2. init_db()         建表（含 agent_usage）；失败只告警，不阻塞启动
    3. warmup()          知识库检索预热（bge-m3 / reranker / Milvus，约 6~10 秒）
    4. build_agent()     组装 agent 放进 app.state 复用；失败则首个请求时懒加载
    5. 初始化管理员      仅在配置了 BOOTSTRAP_ADMIN_* 时执行

前四步全部 fail-open：数据库没起来、Milvus 连不上、模型没配好，服务仍然能启动，
具体接口按需返回 503 / 降级，而不是让整个进程起不来。

**为什么异常处理器要逐个注册**

业务模块自己抛的异常（UserException / LaboratoryException / LibraryException）
都带 `.code`，注册之后统一变成 `{"code": ..., "message": ...}` 信封；
HTTPException / 参数校验 / 未捕获异常也各自有一层，返回给学生的永远是中文人话，
堆栈只进日志。
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import loguru
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi import HTTPException
from starlette.requests import Request

from app.api import api_router
from app.common.exceptions import (
    BusinessException,
    business_exception_handler,
    global_exception_handler,
    http_exception_handler,
    laboratory_exception_handler,
    library_exception_handler,
    model_config_exception_handler,
    user_exception_handler,
    validation_exception_handler,
)
from app.config import settings
from app.database import SessionLocal, init_db
from app.harness.agent import build_agent, warmup
from app.observability import setup_logging
from app.services.laboratory.exceptions import LaboratoryException
from app.services.library.exceptions import LibraryException
from app.services.user import user_services
from app.services.user.exceptions import UserException
from app.utils.trace import get_trace_context, set_trace_context

logger = loguru.logger.bind(name=__name__)

APP_TITLE = "兰州理工大学智能校园助手 API"
APP_VERSION = "0.1.0"


def _bootstrap_admin() -> None:
    """按配置初始化首个管理员（幂等；没配就跳过，代码里不留默认口令）"""
    username = settings.BOOTSTRAP_ADMIN_USERNAME
    password = settings.BOOTSTRAP_ADMIN_PASSWORD
    if not username or not password:
        logger.info("未配置 BOOTSTRAP_ADMIN_USERNAME/PASSWORD，跳过初始管理员")
        return

    try:
        with SessionLocal() as db:
            admin, created = user_services.ensure_initial_admin(
                db,
                username=username,
                password=password,
                name=settings.BOOTSTRAP_ADMIN_NAME,
            )
        logger.info("初始管理员{}：{}", "已创建" if created else "已存在", admin.username)
    except Exception:                            # noqa: BLE001
        logger.exception("初始管理员创建失败（不影响启动，可手动创建）")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动/关闭钩子：每一步都 fail-open，不让单个依赖拖死整个服务"""
    setup_logging()

    try:
        init_db()
    except Exception:                            # noqa: BLE001
        logger.exception("建表失败（数据库未就绪？数据相关接口会返回错误）")

    try:
        warmup()
    except Exception:                            # noqa: BLE001
        logger.exception("知识库预热失败（检索会慢/不可用，其它功能不受影响）")

    try:
        app.state.agent = build_agent()
        logger.info("智能助手已就绪")
    except Exception:                            # noqa: BLE001
        logger.exception("智能助手初始化失败，将在首个 /api/chat 请求时重试")
        app.state.agent = None

    _bootstrap_admin()

    logger.info("服务启动完成：{} v{}（文档 /docs）", APP_TITLE, APP_VERSION)
    yield
    logger.info("服务已关闭")


def create_app() -> FastAPI:
    """应用工厂（测试里可以反复创建，互不影响）"""
    app = FastAPI(
        title=APP_TITLE,
        version=APP_VERSION,
        description="校园规章制度问答 + 实验室/自习室预约（两阶段确认写操作）",
        lifespan=lifespan,
    )

    # ---------------------------------------------------------------- 中间件
    @app.middleware("http")
    async def trace_middleware(request: Request, call_next):
        """每个请求一个 trace_id：日志、LangSmith、响应头 X-Trace-Id 共用"""
        set_trace_context(None)
        response = await call_next(request)
        trace_id = get_trace_context().get("trace_id")
        if trace_id:
            response.headers["X-Trace-Id"] = str(trace_id)
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,   # 生产别用 ["*"]，按前端域名配置
        allow_credentials=True,                # 允许前端带 Authorization
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Trace-Id"],         # 前端读得到 trace id
    )

    # ---------------------------------------------------------------- 异常
    app.add_exception_handler(BusinessException, business_exception_handler)
    app.add_exception_handler(UserException, user_exception_handler)
    app.add_exception_handler(LaboratoryException, laboratory_exception_handler)
    app.add_exception_handler(LibraryException, library_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    # 模型没配好 / provider 包缺失：部署问题，返回 503
    from app.harness.model import ModelConfigError

    app.add_exception_handler(ModelConfigError, model_config_exception_handler)
    app.add_exception_handler(Exception, global_exception_handler)

    # ---------------------------------------------------------------- 路由
    app.include_router(api_router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.getenv("API_HOST", "127.0.0.1"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=False,
    )
