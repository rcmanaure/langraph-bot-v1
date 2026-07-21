FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Install deps — cached layer (only re-runs when pyproject.toml or uv.lock change)
COPY pyproject.toml uv.lock* ./
RUN uv sync --dev
# Fallback: ensure pytest available
RUN pip install -e .[dev] 2>/dev/null || pip install pytest pytest-asyncio

COPY . .
RUN chmod +x /app/entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app
