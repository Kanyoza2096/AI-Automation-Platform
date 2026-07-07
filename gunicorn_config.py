"""Gunicorn configuration."""

import os

# Server socket
bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"

# Worker processes — gthread matches the Procfile and handles I/O concurrency
# within the 512 MB Render free-tier memory budget.
workers = 1
worker_class = "gthread"
threads = 4

# Timeout
timeout = 120
graceful_timeout = 30

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"