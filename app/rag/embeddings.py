"""共享的嵌入模型工厂（进程内单例）

**为什么单独抽出来**

原来 embedding 是在 `app/tool/rag_tool.py::_init()` 里就地构造的，而记忆层
（`app/memory/`）也要用同一个 bge-m3 才能把消息向量和知识库向量放进同一空间。
bge-m3 大约 2GB 显存/内存，每个模块各建一份会直接翻倍 —— 所以：

    rag_tool（知识库检索）  ─┐
                             ├─→ get_embeddings()  ← 进程内只加载一次
    app/memory（短期记忆）  ─┘

**为什么用 `settings.EMBEDDING_MODEL_PATH` 而不是 HF 仓库名**

模型目录已经在仓库里（`models/BAAI/bge-m3`），用本地路径既不走网络、也不依赖
HF 缓存；离线部署时不会因为拉模型失败把整个检索拖死。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

__all__ = ["get_embeddings", "clear_embeddings_cache"]


@lru_cache(maxsize=1)
def get_embeddings() -> Any:
    """返回进程内唯一的 HuggingFaceEmbeddings（bge-m3，1024 维、已归一化）

    函数内 import：没装 torch / sentence-transformers 的环境仍能 import 本模块
    而不炸（第一次真正调用时才需要这些依赖）。
    """
    from langchain_huggingface import HuggingFaceEmbeddings

    from app.config import settings

    return HuggingFaceEmbeddings(
        model_name=str(settings.EMBEDDING_MODEL_PATH),
        model_kwargs={"device": "cpu"},          # 4GB 显存留给 reranker
        encode_kwargs={"normalize_embeddings": True},
    )


def clear_embeddings_cache() -> None:
    """释放缓存的模型（脚本里换模型 / 测试里释放内存用）"""
    get_embeddings.cache_clear()
