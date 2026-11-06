"""统一响应格式（接口层对外契约）

所有接口都返回同一层信封，前端只需要看三个字段：

    {"code": 200, "message": "请求成功", "data": {...}}

为什么不让 FastAPI 直接返回裸对象：
    * 成功 / 失败的结构一致，前端拦截器只写一次；
    * `code` 同时表达业务语义（404 找不到、403 越权、429 太频繁），
      HTTP 状态码仍然按语义设置（见 app/common/exceptions.py）。

分页结构统一用 app/schemas/models.py 里的 PageResponse（total/page/page_size/data）。
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

__all__ = ["Response"]


class Response(BaseModel, Generic[T]):
    """统一响应信封"""

    code: int = Field(200, description="业务码，与 HTTP 状态码一致")
    message: str = Field("请求成功", description="给人看的提示")
    data: T | None = Field(None, description="业务数据")

    @classmethod
    def success(cls, message: str = "请求成功", data: T | None = None) -> "Response[T]":
        return cls(code=200, message=message, data=data)

    @classmethod
    def error(
        cls, code: int = 500, message: str = "请求失败", data: T | None = None
    ) -> "Response[T]":
        return cls(code=code, message=message, data=data)
