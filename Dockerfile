FROM python:3.14-slim-trixie

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    AI_INTERVIEW_ROOT=/app \
    PATH="/app/.venv/bin:$PATH" \
    HF_HOME=/data/hf-cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY web ./web
COPY desktop/renderer ./desktop/renderer
COPY run.py ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["ai-interview", "--api-only", "--host", "0.0.0.0", "--port", "8000"]
