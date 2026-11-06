"""第 4 层组件：重排（cross-encoder reranker）

为什么单独一个文件：
    它有独立的模型生命周期（加载 / 设备 / 显存），而且可以整体替换
    （换模型、或换成 API 版 reranker），层 4 的编排不需要跟着改。

为什么不用 langchain 的 CrossEncoderReranker：
    - 它需要一个 cross-encoder 包装类，而 langchain-community 正在 sunset，
      你装的 0.4.2 里 cross_encoders 已经没有可用的类
    - 这里要做的是「子块重排 + 按 parent_id 聚合到父块」，和它的
      Document 压缩器形状不匹配
    自己写更直接、更可控。

三个刻意的设计：
    1. 懒加载 + 进程内复用：v2-m3 约 568M 参数，加载一次好几秒，
       绝不能每个请求加载一次；上 FastAPI 时用 get_reranker() 在启动阶段预热
    2. 不假设分数的尺度：bge-reranker 默认激活函数到底输出 sigmoid 还是原始
       logit 没有实测确认过，所以这里只保证「越大越相关」，阈值留给评估集校准
    3. 模块导入不碰 torch / sentence-transformers：它们很重，而这个文件要能被
       随手 import（比如给层 4 写测试、注入假 reranker）
"""

from __future__ import annotations

import time
from functools import lru_cache
from typing import Protocol
from app.config import settings

import loguru

logger = loguru.logger.bind(name=__name__)

DEFAULT_MODEL = str(settings.RARANKER_MODEL_PATH)

# 子块实测中位 121 字、最大 369 字，512 足够。
# 不要设到模型上限（8192）：cross-encoder 的开销随序列长度超线性增长。
DEFAULT_MAX_LENGTH = 512

# CPU 上小一点更稳，GPU 上可以调大
DEFAULT_BATCH_SIZE = 16


class SupportsScore(Protocol):
    """重排器接口

    层 4 只依赖这个形状：测试时可以注入假实现（比如按关键词重合度打分），
    评估集做 A/B 时也可以直接传 None 走「不重排」。
    """

    def score(self, query: str, texts: list[str]) -> list[float]: ...


class Reranker:
    """bge-reranker-v2-m3 的封装：query × 文本 → 相关性分数（越大越相关）"""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        *,
        device: str | None = None,          # None = 自动探测（有 CUDA 就用）
        max_length: int = DEFAULT_MAX_LENGTH,
        batch_size: int = DEFAULT_BATCH_SIZE,
        backend: str = "torch",             # 'torch' | 'onnx' | 'openvino'
        local_files_only: bool = False,     # 模型已下到本地时设 True，避免联网探测
        cache_folder: str | None = None,
        model_kwargs: dict | None = None,   # 例如 GPU 上 {"torch_dtype": torch.float16}
    ) -> None:
        self.model_name = model_name
        self.max_length = max_length
        self.batch_size = batch_size
        self.backend = backend
        self.local_files_only = local_files_only
        self.cache_folder = cache_folder
        self.model_kwargs = model_kwargs
        self._device = device
        self._model = None

    # ------------------------------------------------------------ 生命周期
    @property
    def device(self) -> str:
        """自动探测设备；torch 在这里才被导入"""
        if self._device is None:
            try:
                import torch

                self._device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:      # 正常不会发生：sentence-transformers 会带入 torch
                self._device = "cpu"
        return self._device

    @property
    def model(self):
        """懒加载：第一次访问时才真正加载模型"""
        if self._model is None:
            self._model = self._load()
        return self._model

    def _load(self):
        from sentence_transformers import CrossEncoder

        started = time.perf_counter()
        model = CrossEncoder(
            self.model_name,
            device=self.device,
            max_length=self.max_length,
            backend=self.backend,
            local_files_only=self.local_files_only,
            cache_folder=self.cache_folder,
            model_kwargs=self.model_kwargs,
        )
        logger.info(
            "重排模型已加载：{} device={} max_length={} backend={} 耗时 {:.1f}s",
            self.model_name, self.device, self.max_length, self.backend,
            time.perf_counter() - started,
        )
        return model

    def warmup(self) -> None:
        """提前加载，把几秒的耗时落在进程启动阶段，而不是第一个用户身上"""
        _ = self.model

    # ------------------------------------------------------------ 打分
    def score(self, query: str, texts: list[str], *,
              batch_size: int | None = None) -> list[float]:
        """给每个 (query, text) 打分，返回与 texts 同序的分数

        分数只保证「越大越相关」，不要依赖它的绝对值范围。

        注意：超过 max_length 的部分会被**静默截断**。子块约 300 字，512 够用；
        如果哪天拿父块（1000~1500 字）来重排，就会被截掉尾部——这也是当初
        选「重排子块」而不是「重排父块」的原因之一。
        """
        if not texts:
            return []
        started = time.perf_counter()
        raw = self.model.predict(
            [(query, text) for text in texts],
            batch_size=batch_size or self.batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        try:
            scores = [float(raw[i]) for i in range(len(raw))]
        except TypeError as exc:
            raise ValueError(
                "重排模型返回的不是一维分数，检查模型配置里的 num_labels 是否为 1"
            ) from exc
        if len(scores) != len(texts):
            raise ValueError(
                f"重排模型返回 {len(scores)} 个分数，但候选有 {len(texts)} 个"
            )

        logger.debug(
            "重排 {} 个候选，耗时 {:.0f}ms",
            len(texts), (time.perf_counter() - started) * 1000,
        )
        return scores

    def rank(self, query: str, texts: list[str], *, top_k: int | None = None,
             batch_size: int | None = None) -> list[tuple[int, float]]:
        """打分并按分数降序返回 (原下标, 分数)

        只是手工试验时方便；层 4 的聚合需要「与候选同序」的分数，用 score()。
        """
        scores = self.score(query, texts, batch_size=batch_size)
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        if top_k is not None:
            order = order[:top_k]
        return [(i, scores[i]) for i in order]


@lru_cache(maxsize=4)
def get_reranker(
    model_name: str = DEFAULT_MODEL,
    *,
    device: str | None = None,
    max_length: int = DEFAULT_MAX_LENGTH,
    batch_size: int = DEFAULT_BATCH_SIZE,
    backend: str = "torch",
    local_files_only: bool = False,
) -> Reranker:
    """进程内复用同一个重排器（相同参数只会加载一次）

    FastAPI 里在 lifespan / startup 调一次做预热：

        @asynccontextmanager
        async def lifespan(app):
            get_reranker()          # 启动时把模型加载好
            yield
    """
    reranker = Reranker(
        model_name,
        device=device,
        max_length=max_length,
        batch_size=batch_size,
        backend=backend,
        local_files_only=local_files_only,
    )
    reranker.warmup()
    return reranker