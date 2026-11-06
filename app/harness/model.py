"""模型工厂：主模型（决策第 3 层）与轻量意图模型（决策第 2 层）

**两个角色，共用一个工厂**

    主模型     get_main_model()    —— 主 Agent 的脑子，负责最终决定要不要调检索、怎么答
    意图模型   get_intent_model()  —— 只在规则层没命中时跑一次二分类，要求便宜、快、稳

意图模型现在**暂时也指向 DeepSeek**（`.env` 里的 DEEPSEEK_*），
先用同一个 provider 跑通流程；以后换小模型（本地 Qwen / 通义 / 更便宜的档位）时，
只需要在 `.env` 里加 `INTENT_MODEL` / `INTENT_BASE_URL` / `INTENT_API_KEY` / `INTENT_PROVIDER`
或改这里的默认值，**决策层的代码一行都不用动** —— 这也是把它抽成工厂的原因。

**provider 解析**

DeepSeek 走 `langchain-deepseek`（`model_provider="deepseek"`）；
如果该包没装但装了 `langchain-openai`，就退回 OpenAI 兼容端点（DeepSeek 的
`/v1/chat/completions` 本身就是兼容的）。两个包都没有时抛一个能照着做的错，
而不是抛一句没头没尾的 ImportError —— 这是最容易卡住新人的一步。
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv

# 不覆盖已存在的系统环境变量，便于部署时用环境变量覆盖 .env
load_dotenv(override=False)

INSTALL_HINT = (
    "没有可用的 LLM provider 包。请任选其一安装：\n"
    "    uv pip install --python .\\.venv\\Scripts\\python.exe langchain-deepseek\n"
    "    uv pip install --python .\\.venv\\Scripts\\python.exe langchain-openai"
)


class ModelConfigError(RuntimeError):
    """模型配置缺失 / provider 包缺失（拿到这个错应该能直接照着提示修）"""


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _build(provider: str, model: str, api_key: str, base_url: str | None, **kwargs: Any):
    """按 provider 建模型，缺包时在 deepseek ↔ openai 兼容之间回退"""
    from langchain.chat_models import init_chat_model

    attempts: list[tuple[str, str | None]] = [(provider, None)]
    if provider != "openai":
        attempts.append(("openai", base_url))

    errors: list[str] = []
    for attempt_provider, fallback_base in attempts:
        try:
            return init_chat_model(
                model=model,
                model_provider=attempt_provider,
                api_key=api_key,
                base_url=fallback_base or base_url,
                **kwargs,
            )
        except ImportError as exc:                       # 缺 provider 包
            errors.append(f"{attempt_provider}: {exc}")
        except Exception as exc:                         # 配置类错误（如 api_key 为空）
            raise ModelConfigError(f"初始化 {attempt_provider} 模型失败：{exc}") from exc

    raise ModelConfigError(
        f"模型 {model}（provider={provider}）初始化失败：\n  "
        + "\n  ".join(errors)
        + f"\n{INSTALL_HINT}"
    )


def _require(name: str) -> str:
    value = _env(name)
    if not value:
        raise ModelConfigError(f".env 里缺少 {name}（模型工厂需要它才能建模型）")
    return value


@lru_cache(maxsize=4)
def get_main_model(temperature: float = 0.3):
    """决策第 3 层的模型：主 Agent 用来决定要不要调检索、并生成最终答案

    为什么不用 temperature=0：答案里要组织条款、给建议，稍微留一点自由度更自然；
    真正需要确定性的是第 2 层（见下）。

    失败处理（生产必需档）在这里配三样：
    - `timeout`：单次调用最多等多久（默认 60s，`HARNESS_MODEL_TIMEOUT`）；
    - `max_retries`：429/5xx/连接错误由 SDK 自动指数退避重试（默认 2，
      `HARNESS_MODEL_RETRIES`）；401/400 这类不可重试的错误会直接抛，由上层翻译；
    - 重试耗尽后的兜底：中间件把异常转成学生能看懂的一句话（app/harness/middleware.py）。
    """
    return _build(
        provider=_env("DEEPSEEK_PROVIDER", "deepseek") or "deepseek",
        model=_require("DEEPSEEK_MODEL"),
        api_key=_require("DEEPSEEK_API_KEY"),
        base_url=_env("DEEPSEEK_BASE_URL"),
        temperature=temperature,
        timeout=float(_env("HARNESS_MODEL_TIMEOUT", "60") or 60),
        max_retries=int(_env("HARNESS_MODEL_RETRIES", "2") or 2),
        extra_body={"thinking": {"type": "disabled"}},
    )


@lru_cache(maxsize=4)
def get_intent_model():
    """决策第 2 层的轻量模型：只输出一个 JSON，temperature=0、有超时上限

    三个刻意的参数：
    - `temperature=0`：分类任务要可复现，同一句话不应该时而查时而不查；
    - `max_tokens` 小：它的输出就是一个 JSON，给大预算只会变慢；
    - `timeout` + `max_retries`：它挡在用户和主模型之间，**绝不能把它自己变成故障点**
      （超时/报错由 decision.py 的 fail-open 兜住）。
    """
    provider = _env("INTENT_PROVIDER", _env("DEEPSEEK_PROVIDER", "deepseek")) or "deepseek"
    # 换模型时优先读 INTENT_*，没配就退回 DEEPSEEK_*（现在就是这个状态）
    return _build(
        provider=provider,
        model=_env("INTENT_MODEL", _env("DEEPSEEK_MODEL")) or _require("DEEPSEEK_MODEL"),
        api_key=_env("INTENT_API_KEY", _env("DEEPSEEK_API_KEY")) or _require("DEEPSEEK_API_KEY"),
        base_url=_env("INTENT_BASE_URL", _env("DEEPSEEK_BASE_URL")),
        temperature=0,
        max_tokens=200,
        timeout=float(_env("INTENT_TIMEOUT", "8") or 8),
        max_retries=int(_env("INTENT_MAX_RETRIES", "1") or 1),
        extra_body={"thinking": {"type": "disabled"}},
    )


def get_deepseek_model():
    """兼容旧调用（app/harness/agent.py 之前用的名字）"""
    return get_main_model()


# ------------------------------------------------------------------ 备用模型（降级）
#
# 失败处理三件套里的「② 降级」：主模型在**运行时**不可用（超时 / 5xx / 连接断 /
# 限流）时，换一个模型把这一轮答完，而不是把 500 甩给学生。
#
# 为什么不用「provider 装包回退」那套：那个只在 ImportError（部署缺包）时生效，
# 覆盖不了「包在、网断了」。这里要的是同一条链路里的第二次尝试。
#
# 没配 FALLBACK_MODEL 时返回 None —— 调用方据此决定「直接抛」还是「降级」，
# 不会出现「降级到同一个模型」这种假动作。

FALLBACK_ENABLED = os.getenv("HARNESS_MODEL_FALLBACK_ENABLED", "1").strip().lower() not in (
    "0", "false", "no", "off",
)


@lru_cache(maxsize=2)
def get_fallback_model():
    """备用模型；未配置时返回 None

    默认继承 DEEPSEEK_*（同 provider 的不同模型也算降级：小模型往往更稳、更便宜）。
    要换 provider（例如通义 / 本地 Qwen），配 FALLBACK_PROVIDER / FALLBACK_BASE_URL
    即可，agent 侧一行都不用改。
    """
    if not FALLBACK_ENABLED:
        return None

    model = _env("FALLBACK_MODEL")
    if not model:
        return None

    provider = _env("FALLBACK_PROVIDER", _env("DEEPSEEK_PROVIDER", "deepseek")) or "deepseek"
    return _build(
        provider=provider,
        model=model,
        api_key=_env("FALLBACK_API_KEY", _env("DEEPSEEK_API_KEY")) or _require("DEEPSEEK_API_KEY"),
        base_url=_env("FALLBACK_BASE_URL", _env("DEEPSEEK_BASE_URL")),
        temperature=0.3,
        timeout=float(_env("HARNESS_MODEL_TIMEOUT", "60") or 60),
        # 备用模型本身只试一次：它已经是第二道防线，再重试就是让学生干等
        max_retries=0,
        extra_body={"thinking": {"type": "disabled"}},
    )


def fallback_model_name() -> str | None:
    """备用模型的名字（打日志 / 写响应字段用；没配置返回 None）"""
    if not FALLBACK_ENABLED:
        return None
    model = _env("FALLBACK_MODEL")
    if not model:
        return None
    provider = _env("FALLBACK_PROVIDER", _env("DEEPSEEK_PROVIDER", "deepseek")) or "deepseek"
    return f"{provider}:{model}"


def describe_models() -> dict[str, str]:
    """打日志用：当前各角色分别指向哪个模型（不打印密钥）"""
    return {
        "main": f"{_env('DEEPSEEK_PROVIDER', 'deepseek')}:{_env('DEEPSEEK_MODEL')}",
        "intent": (
            f"{_env('INTENT_PROVIDER', _env('DEEPSEEK_PROVIDER', 'deepseek'))}:"
            f"{_env('INTENT_MODEL', _env('DEEPSEEK_MODEL'))}"
        ),
        "fallback": fallback_model_name() or "未配置",
    }
