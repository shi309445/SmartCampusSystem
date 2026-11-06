"""agent 工具集

    rag_tool         校园规章制度知识库检索（RAG 的唯一入口）
    reservation_tool 实验室 / 图书馆自习室预约（唯一预约入口 + 查询配套工具）

主 Agent 的默认工具列表是 `user_tool`（app/harness/agent.py 用它），
两个模块的工具都汇总在里面；中间件的 ALLOWED_TOOLS 白名单也引用同一份名字，
加工具时只需要改这里和 reservation_tool.RESERVATION_TOOL_NAMES。
"""

from .rag_tool import retrieval_knowledge_base, warmup_knowledge_base
from .reservation_tool import (
    RESERVATION_TOOL_NAMES,
    WRITE_TOOL_NAMES,
    cancel_my_reservation,
    cancel_reservation_for_user,
    describe_write_action,
    find_available_seats,
    list_lab_slots,
    list_my_reservations,
    list_reservations_for_user,
    make_reservation,
    propose_write_action,
    reservation_tools,
    search_labs,
    search_study_rooms,
    submit_reservation,
)

# 主 Agent 的默认工具列表
user_tool = [retrieval_knowledge_base, *reservation_tools]

__all__ = [
    "retrieval_knowledge_base",
    "warmup_knowledge_base",
    "RESERVATION_TOOL_NAMES",
    "WRITE_TOOL_NAMES",
    "cancel_my_reservation",
    "cancel_reservation_for_user",
    "describe_write_action",
    "find_available_seats",
    "list_lab_slots",
    "list_my_reservations",
    "list_reservations_for_user",
    "make_reservation",
    "propose_write_action",
    "reservation_tools",
    "search_labs",
    "search_study_rooms",
    "submit_reservation",
    "user_tool",
]
