"""管理员接口：预算用量查看（可观测性的「查看」最小实现）

目前只做「某个学生今天用了多少 token / 调用」——排查「为什么他被限流」
「谁的额度快用完了」时最需要的一眼。更完整的 Dashboard / 指标聚合属于后续。
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends

from app.common.response import Response
from app.dependencies.auth import require_admin
from app.harness.budget import budget, get_daily
from app.schemas.chat import UsageOut
from app.schemas.table import User

router = APIRouter(prefix="/api/admin", tags=["管理员"])

__all__ = ["router"]


@router.get(
    "/usage/{user_id}",
    response_model=Response[UsageOut],
    summary="查看某个学生今天的 token / 调用用量",
)
def usage(
    user_id: int,
    _: User = Depends(require_admin),
) -> Response[UsageOut]:
    tokens, calls = get_daily(user_id)
    return Response.success(
        data=UsageOut(
            user_id=user_id,
            date=date.today(),
            tokens=tokens,
            calls=calls,
            daily_tokens_limit=budget.daily_tokens,
            daily_calls_limit=budget.daily_calls,
        )
    )
