# Quant Terminal - Dockerfile
# 支持CPU和GPU版本 (通过build-arg GPU_SUPPORT控制)

ARG PYTHON_VERSION=3.11
ARG GPU_SUPPORT=false

# ============================================================================
# 基础镜像选择
# ============================================================================
FROM python:${PYTHON_VERSION}-slim as base

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 安装Poetry
ENV POETRY_VERSION=1.7.1
ENV POETRY_HOME=/opt/poetry
ENV POETRY_VENV=/opt/poetry-venv
ENV POETRY_CACHE_DIR=/opt/.cache

RUN python3 -m venv $POETRY_VENV \
    && $POETRY_VENV/bin/pip install -U pip setuptools \
    && $POETRY_VENV/bin/pip install poetry==${POETRY_VERSION}

ENV PATH="${PATH}:${POETRY_VENV}/bin"

# ============================================================================
# 依赖安装阶段
# ============================================================================
FROM base as deps

# 复制依赖文件
COPY pyproject.toml poetry.lock* ./

# 安装依赖 (根据GPU_SUPPORT安装不同版本的PyTorch)
ARG GPU_SUPPORT
RUN if [ "$GPU_SUPPORT" = "true" ]; then \
        poetry install --no-interaction --no-ansi --no-root --with gpu -E all; \
    else \
        poetry install --no-interaction --no-ansi --no-root -E all; \
    fi

# ============================================================================
# GPU版本 (CUDA支持)
# ============================================================================
FROM nvidia/cuda:12.1-runtime-ubuntu22.04 as gpu-base

ARG PYTHON_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends \
    python${PYTHON_VERSION} \
    python3-pip \
    python3-venv \
    build-essential \
    curl \
    git \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN ln -s /usr/bin/python${PYTHON_VERSION} /usr/bin/python

WORKDIR /app

# 安装Poetry
ENV POETRY_VERSION=1.7.1
ENV POETRY_HOME=/opt/poetry
ENV POETRY_VENV=/opt/poetry-venv
RUN python3 -m venv $POETRY_VENV \
    && $POETRY_VENV/bin/pip install poetry==${POETRY_VERSION}
ENV PATH="${PATH}:${POETRY_VENV}/bin"

# ============================================================================
# 生产镜像 (CPU)
# ============================================================================
FROM base as production-cpu

WORKDIR /app

# 从deps阶段复制虚拟环境
COPY --from=deps /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}"

# 复制应用代码
COPY backend/ ./backend/
COPY tests/ ./tests/
COPY README.md ./

# 设置Python路径
ENV PYTHONPATH=/app/backend
ENV PYTHONUNBUFFERED=1
ENV LOG_LEVEL=INFO

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ============================================================================
# 生产镜像 (GPU)
# ============================================================================
FROM gpu-base as production-gpu

WORKDIR /app

# 从deps阶段复制虚拟环境
COPY --from=deps /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}"

# 复制应用代码
COPY backend/ ./backend/
COPY tests/ ./tests/
COPY README.md ./

# 设置环境变量
ENV PYTHONPATH=/app/backend
ENV PYTHONUNBUFFERED=1
ENV LOG_LEVEL=INFO
ENV NVIDIA_VISIBLE_DEVICES=all
ENV CUDA_VISIBLE_DEVICES=0

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}')"

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ============================================================================
# 开发镜像
# ============================================================================
FROM base as development

WORKDIR /app

# 安装开发依赖
COPY pyproject.toml poetry.lock* ./
RUN poetry install --no-interaction --no-ansi --no-root --with dev -E all

# 复制应用代码
COPY backend/ ./backend/
COPY tests/ ./tests/
COPY README.md ./

ENV PYTHONPATH=/app/backend
ENV PYTHONUNBUFFERED=1
ENV LOG_LEVEL=DEBUG

# 开发模式：热重载
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ============================================================================
# 最终镜像选择 (通过target参数选择)
# ============================================================================
FROM production-${GPU_SUPPORT:+gpu}${GPU_SUPPORT:-cpu} as final
