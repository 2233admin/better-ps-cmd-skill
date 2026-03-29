# Quant Terminal - 生产级量化交易系统 Dockerfile
# 支持CPU和GPU版本

# =============================================================================
# 基础镜像
# =============================================================================
FROM python:3.11-slim as base

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_VERSION=1.7.0 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VENV_IN_PROJECT=1

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    git \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 安装Poetry
RUN pip install "poetry==$POETRY_VERSION"

# 设置工作目录
WORKDIR /app

# 复制项目文件
COPY pyproject.toml README.md ./
COPY src/ ./src/

# =============================================================================
# CPU生产环境
# =============================================================================
FROM base as production-cpu

# 安装依赖（不包含GPU相关包）
RUN poetry install --no-dev --no-root --extras "api data"

# 安装项目
RUN poetry install --no-dev

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD poetry run python -c "import quant_terminal; print('OK')" || exit 1

# 启动命令
CMD ["poetry", "run", "uvicorn", "quant_terminal.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# =============================================================================
# GPU生产环境
# =============================================================================
FROM nvidia/cuda:12.1.0-runtime-ubuntu22.04 as production-gpu

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=1.7.0 \
    POETRY_NO_INTERACTION=1

# 安装Python和依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3-pip \
    python3.11-venv \
    gcc \
    g++ \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 安装Poetry
RUN pip3 install "poetry==$POETRY_VERSION"

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/

# 安装所有依赖（包含GPU）
RUN poetry install --no-dev --no-root --extras "all"
RUN poetry install --no-dev

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD poetry run python -c "import torch; print(f'CUDA: {torch.cuda.is_available()}')" || exit 1

CMD ["poetry", "run", "uvicorn", "quant_terminal.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# =============================================================================
# 开发环境
# =============================================================================
FROM base as development

# 安装所有依赖（包含开发依赖）
RUN poetry install --no-root
RUN poetry install

# 安装Jupyter（可选）
RUN poetry add --group dev jupyter ipykernel

EXPOSE 8000 8888

CMD ["poetry", "run", "uvicorn", "quant_terminal.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
