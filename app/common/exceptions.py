"""接口层统一异常与处理器

**异常从哪来**

    业务模块自己的异常（已有 .code，直接当 HTTP 状态码用）：
        app.services.user.exceptions.UserException          401/403/404/409/400
        app.services.laboratory.exceptions.LaboratoryException
        app.services.library.exceptions.LibraryException

    接口层自己的异常（本文件）：
        BusinessException  接口层的业务规则（如「智能助手未就绪」「请求太频繁」）

    框架异常：
        HTTPException / RequestValidationError / 未捕获 Exception

**为什么要统一**

    * 前端只处理一种结构（见 app/common/response.py 的 Response）；
    * 关键错误全部打日志并带 trace_id，排查时能对上一轮 agent 的日志；
    * 未捕获异常绝不把堆栈或 SQL 细节返回给学生。
"""

from __future__ import annotations

from typing import Any

import loguru
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.common.response import Response
from app.harness.model import ModelConfigError
from app.services.laboratory.exceptions import LaboratoryException
from app.services.library.exceptions import LibraryException
from app.services.user.exceptions import UserException

logger = loguru.logger.bind(name=__name__)

__all__ = [
    "BusinessException",
    "business_exception_handler",
    "user_exception_handler",
    "laboratory_exception_handler",
    "library_exception_handler",
    "model_config_exception_handler",
    "http_exception_handler",
    "validation_exception_handler",
    "global_exception_handler",
]


class BusinessException(Exception):
    """接口层业务异常（code 同时是 HTTP 状态码）"""

    def __init__(self, message: str, code: int = 400) -> None:
        self.message = message
        self.code = code
        super().__init__(message)


def _json(status_code: int, message: str, *, code: int | None = None,
          data: Any = None, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=Response.error(code=code or status_code, message=message, data=data).model_dump(),
        headers=headers,
    )


async def business_exception_handler(request: Request, exc: BusinessException) -> JSONResponse:
    logger.warning("业务异常：{} {} -> [{}] {}", request.method, request.url.path, exc.code, exc.message)
    return _json(exc.code, exc.message)


async def user_exception_handler(request: Request, exc: UserException) -> JSONResponse:
    """用户业务异常（登录失效 / 越权 / 账号冲突……）"""
    logger.warning("用户异常：{} {} -> [{}] {}", request.method, request.url.path, exc.code, exc.message)
    headers = {"WWW-Authenticate": "Bearer"} if exc.code == 401 else None
    return _json(exc.code, exc.message, headers=headers)


async def laboratory_exception_handler(
    request: Request, exc: LaboratoryException
) -> JSONResponse:
    logger.warning("实验室业务异常：{} {} -> [{}] {}", request.method, request.url.path, exc.code, exc.message)
    return _json(exc.code, exc.message)


async def library_exception_handler(request: Request, exc: LibraryException) -> JSONResponse:
    logger.warning("图书馆业务异常：{} {} -> [{}] {}", request.method, request.url.path, exc.code, exc.message)
    return _json(exc.code, exc.message)


async def model_config_exception_handler(
    request: Request, exc: ModelConfigError
) -> JSONResponse:
    """模型没配好 / provider 包缺失：这是部署问题，给 503 并留下明确日志"""
    logger.error("模型配置错误：{}", exc)
    return _json(503, "智能助手暂时不可用（模型配置有误），请联系管理员")


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return _json(exc.status_code, detail, headers=getattr(exc, "headers", None))


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """参数校验失败：把字段路径一起返回，前端能直接定位到哪个字段"""
    errors = [
        {
            "loc": list(error.get("loc", ())),
            "msg": error.get("msg", ""),
            "type": error.get("type", ""),
        }
        for error in exc.errors()
    ]
    logger.info("参数校验失败：{} {}", request.method, request.url.path)
    return _json(422, "请求参数校验错误", data=errors)


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底：任何未捕获异常都只回一句人话，堆栈只进日志"""
    logger.exception("未处理异常：{} {}", request.method, request.url.path)
    return _json(500, "服务器内部错误，请稍后再试")
