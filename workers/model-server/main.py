"""
main.py — Entrypoint for the Model Server.

Boots the FastAPI application (defined in server.py) via Uvicorn.
All model loading is handled by the FastAPI lifespan event in server.py,
so this file is intentionally minimal.

Environment variables:
    MODEL_SERVER_HOST   Bind address (default: 0.0.0.0).
    MODEL_SERVER_PORT   Bind port    (default: 8001).
    MODEL_SERVER_WORKERS  Number of Uvicorn worker processes (default: 1).
                          Keep at 1 when running on GPU to avoid loading the
                          model into multiple processes simultaneously.
    LOG_LEVEL           Uvicorn log level: debug | info | warning (default: info).

Usage:
    python main.py
    # or via uvicorn directly:
    uvicorn main:app --host 0.0.0.0 --port 8001
"""

import os

os.environ["HF_HOME"] = os.environ.get("HF_HOME", "/mnt/e/Obsidian/models")

import uvicorn

from server import app  # noqa: F401 — re-exported so `uvicorn main:app` works

if __name__ == "__main__":
    host = os.environ.get("MODEL_SERVER_HOST", "0.0.0.0")
    port = int(os.environ.get("MODEL_SERVER_PORT", "8001"))
    workers = int(os.environ.get("MODEL_SERVER_WORKERS", "1"))
    log_level = os.environ.get("LOG_LEVEL", "info").lower()

    uvicorn.run(
        "server:app",
        host=host,
        port=port,
        workers=workers,
        log_level=log_level,
        # Reload is intentionally disabled; use Docker restart policies instead.
        reload=False,
    )
