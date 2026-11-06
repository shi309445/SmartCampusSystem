"""系统接口：健康检查 / 根路径

健康检查是给运维（docker healthcheck、负载均衡探针、人肉排障）看的，
所以**要能看出现有依赖的真实状态**，而不是只读环境变量：

    之前的问题：`memory: "on" if MEMORY_ENABLED else "off"`
        —— MEMORY_ENABLED 只是「有没有被配置禁用」，Milvus 挂掉时它依然是 on。
        探针据此判断"一切正常"，等于探活失真。

现在的探测与成本：

    database     SELECT 1                        每次真实执行（最便宜也最重要，不缓存）
    memory       Milvus 里有没有记忆 collection   真实探测，结果缓存 30 秒
    knowledge    Milvus 里有没有 rag_child_chunks 真实探测，与 memory 共用一次连接
    model        模型配置是否完整（不发起网络请求） 只判断 key / model 有没有配齐
    agent        进程内 agent 对象是否已就绪        内存判断

**为什么不探"模型能不能真的回话"**：那要花一次 token 和 1~2 秒，探针每 10 秒打一次
就会持续烧钱。所以模型这一项只报"配置是否完整"；真要验证连通性，发一条 /api/chat。

**status 的口径**：数据库不通 = degraded（不能对外服务）；Milvus 不通 = degraded
（问答还能用，但不能检索、没有记忆）。这两种都算"部分能力缺失"，具体缺哪块看字段。
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any, Callable

import loguru
from fastapi import APIRouter, Request
from sqlalchemy import text

from app.common.response import Response
from app.database import SessionLocal
from app.memory import MEMORY_ENABLED

logger = loguru.logger.bind(name=__name__)

router = APIRouter(tags=["系统"])

__all__ = ["router", "reset_probe_cache"]

APP_VERSION = "0.1.0"

# RAG 子块 collection（与 app/rag/layer_3_storage.py 的 COLLECTION_NAME 保持一致）
KNOWLEDGE_COLLECTION = "rag_child_chunks"

# 探测结果缓存：/health 可能被探针高频调用，不能每次都去连 Milvus
CACHE_SECONDS = float(os.getenv("HEALTH_CACHE_SECONDS", "30") or 30)
# 单次 Milvus 探测的超时：探针不能卡住
MILVUS_TIMEOUT = float(os.getenv("HEALTH_MILVUS_TIMEOUT", "2") or 2)

_probe_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, probe: Callable[[], Any]) -> Any:
    """带 TTL 的探测缓存（单进程够用；多副本各自缓存也没问题）"""
    now = time.monotonic()
    cached = _probe_cache.get(key)
    if cached is not None and now - cached[0] < CACHE_SECONDS:
        return cached[1]
    try:
        value = probe()
    except Exception as exc:                     # noqa: BLE001 —— 探针自己绝不能抛
        logger.warning("健康探测 {} 失败：{}", key, exc)
        value = None
    _probe_cache[key] = (now, value)
    return value


# ---------------------------------------------------------------- 各项探测


def _database_ok() -> bool:
    """数据库连通性（不缓存：它是最重要的信号，必须实时）"""
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return True
    except Exception as exc:                     # noqa: BLE001
        logger.warning("健康检查：数据库不可用：{}", exc)
        return False


def _milvus_collections() -> set[str] | None:
    """列出 Milvus 里的 collection；连不上返回 None（区分"连不上"和"没有这个表"）"""
    from app.memory.store import DEFAULT_URI, TOKEN
    from pymilvus import MilvusClient

    kwargs: dict[str, Any] = {"uri": DEFAULT_URI, "timeout": MILVUS_TIMEOUT}
    if TOKEN:
        kwargs["token"] = TOKEN
    client = MilvusClient(**kwargs)
    return set(client.list_collections())


def _memory_state() -> str:
    """短记忆状态：off（配置关掉）/ ok / missing（表没建）/ down（Milvus 不通）"""
    if not MEMORY_ENABLED:
        return "off"

    from app.memory.store import COLLECTION_NAME

    collections = _cached("milvus", _milvus_collections)
    if collections is None:
        return "down"
    return "ok" if COLLECTION_NAME in collections else "missing"


def _knowledge_state() -> str:
    """知识库状态：ok（collection 在）/ missing（还没建索引）/ down（Milvus 不通）"""
    collections = _cached("milvus", _milvus_collections)
    if collections is None:
        return "down"
    return "ok" if KNOWLEDGE_COLLECTION in collections else "missing"


def _model_config_state() -> str:
    """模型配置是否完整（不发起网络请求，只验证能否构建出模型对象）"""
    from app.harness.model import ModelConfigError, get_main_model

    try:
        get_main_model()
    except ModelConfigError as exc:
        logger.warning("健康检查：模型配置不完整：{}", exc)
        return "missing"
    except Exception as exc:                     # noqa: BLE001
        logger.warning("健康检查：模型构建异常：{}", exc)
        return "error"
    return "ok"


def reset_probe_cache() -> None:
    """清空探测缓存（测试用）"""
    _probe_cache.clear()


# ---------------------------------------------------------------- 路由


@router.get("/health", response_model=Response[dict[str, Any]], summary="健康检查")
def health(request: Request) -> Response[dict[str, Any]]:
    database = _database_ok()                   # 不缓存，实时探
    memory = _cached("memory", _memory_state)
    knowledge = _cached("knowledge", _knowledge_state)
    model_config = _cached("model_config", _model_config_state)
    agent_ready = getattr(request.app.state, "agent", None) is not None

    # 知识库 missing 不算故障（可以只跑对话、还没建索引），Milvus down 才算
    healthy = database and memory in ("ok", "off") and knowledge in ("ok", "missing")
    return Response.success(
        data={
            "status": "ok" if healthy else "degraded",
            "version": APP_VERSION,
            "database": "ok" if database else "down",
            "memory": memory,
            "knowledge": knowledge,
            "model": model_config,
            "agent": "ready" if agent_ready else "not_ready",
            "checked_at": datetime.now().isoformat(timespec="seconds"),
        }
    )


@router.get("/", response_model=Response[dict[str, str]], summary="服务信息")
def root() -> Response[dict[str, str]]:
    return Response.success(
        data={
            "name": "兰州理工大学智能校园助手 API",
            "version": APP_VERSION,
            "docs": "/docs",
        }
    )
