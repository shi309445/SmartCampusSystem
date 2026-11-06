"""全项目通用的数据模型（Pydantic）

存放原则（与建表约定一致）：
    * 被多个业务模块共用的「数据模型」放在本文件：app/schemas/models.py
    * 只服务于某个业务模块的数据模型，放在该模块内部，
      例如实验室业务：app/services/laboratory/models.py
"""

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMBase(BaseModel):
    """所有由 ORM 对象转换而来的响应模型的公共基类

    带 from_attributes=True 后可以直接 Model.model_validate(orm_obj)。
    """

    model_config = ConfigDict(from_attributes=True)


class RequestBase(BaseModel):
    """所有请求模型（上层传进来的入参）的公共基类

    extra="forbid"：多传了未声明的字段直接报参数错误，而不是被静默忽略。
    这一点在写接口上很重要——比如用户自己改资料时传了 role=admin，
    静默忽略虽然不会越权，但调用方以为自己改成功了，反而更难排查。
    """

    model_config = ConfigDict(extra="forbid")


class PageResponse(BaseModel, Generic[T]):
    """通用分页响应模型

    total 是「满足条件的总条数」，不是当前页条数；
    data 是当前页的数据列表。
    """

    total: int = Field(0, description="满足条件的总条数")
    page: int = Field(1, description="当前页码")
    page_size: int = Field(10, description="每页条数")
    data: list[T] = Field(default_factory=list, description="当前页数据")
