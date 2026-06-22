#!/bin/sh
set -e

# Alembic MUST run before FastAPI starts.
# checkpointer.setup() (LangGraph tables) runs inside the FastAPI lifespan, after this.
alembic upgrade head

# APScheduler requires single worker — do not increase --workers without migrating to Redis checkpointer (see TODO-REDIS)
exec uvicorn app.main:app \
    --workers 1 \
    --host 0.0.0.0 \
    --port 8000 \
    --timeout-keep-alive 120
