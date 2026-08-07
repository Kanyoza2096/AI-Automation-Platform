"""Gunicorn configuration for FastAPI/ASGI."""

import os

# Server socket
bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"

# Worker processes — UvicornWorker for FastAPI ASGI application
workers = 1
worker_class = "uvicorn.workers.UvicornWorker"

# Timeout
timeout = 120
graceful_timeout = 30

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"