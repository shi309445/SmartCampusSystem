"""app.observability —— 可观测性（采集 / 查看 / 回放）

落地的是六层图里「可观测性 ①②③⑤」这一档，**不引入 Prometheus / Dashboard**：

    setup_logging()    控制台 + 结构化 JSONL 落盘，每条日志自动带 trace_id
    app/utils/trace.py trace_id 生成、ContextVar 传递、LangSmith metadata 对齐
    spans.py           回合汇总（span=turn）+ 每工具/每模型 span + 耗时与 token 归因
    replay.py          按 trace_id 把一轮的时间线还原出来（只读，不重跑模型）

**分工**

    spans.py    运行时埋点：一轮一条汇总，一次工具/模型调用一条 span
    replay.py   排障时用：`python -m app.observability.replay <trace_id>`

**还没做的（进阶档）**

Trace 树 UI、Metric（Prometheus）、自动告警、按 span 的成本报表。
理由见 data/生产必需档补齐需求.md 附录 A：单机、1 人维护，JSONL + replay 够用。

用法（CLI / FastAPI lifespan 各调一次即可，幂等）：

    from app.observability import setup_logging
    setup_logging()
"""

from __future__ import annotations

# 先补追踪隐私默认值，再导入其余模块：LangSmith 在首次上报时读取这些开关。
from app.observability.privacy import harden_tracing

harden_tracing()

from app.observability import spans  # noqa: E402
from app.observability.logging import LOG_DIR, setup_logging  # noqa: E402

__all__ = ["LOG_DIR", "spans", "setup_logging"]
