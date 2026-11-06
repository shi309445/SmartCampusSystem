# SmartCampusSystem 运行镜像
#
# 只装「跑服务」需要的东西：
#   app/          代码
#   docs/         知识库源文件（app/rag/layer_1_data.py 的 DOCS_DIR 指向它）
#   scripts/      测试数据 / 冒烟脚本（容器里也能用）
#   create_table.py
#
# 不进镜像的：models/（4GB 权重，用 volume 挂进来）、.venv、tests、data、get_data、
# logs/（运行期写，用 volume 挂出去）。见 .dockerignore。
#
# 构建：
#   docker build -t smart-campus-system:0.1.0 .
# 只想换 torch 源（默认 CPU 版，避免拖 2GB 的 CUDA 依赖）：
#   docker build --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu121 -t scs:cuda .

FROM python:3.14-slim

# libgomp1: torch 的 OpenMP 运行时；curl: HEALTHCHECK 用
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 curl \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# torch 先单独装：用 CPU 轮子，体积和镜像层都小很多
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
RUN pip install torch --index-url "${TORCH_INDEX_URL}"

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app ./app
COPY docs ./docs
COPY scripts ./scripts
COPY create_table.py ./

EXPOSE 8000

# /health 在依赖降级时仍然返回 200（degraded 体现在 body 里），
# 所以这里只判断"进程还能对外服务"，不会因为 Milvus 挂了就把容器判死。
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health > /dev/null || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
