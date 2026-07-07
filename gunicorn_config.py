"""Gunicorn configuration."""

import os

# Server socket
bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"

# Worker processes
workers = 1  # Render free tier
worker_class = "sync"
threads = 2

# Timeout
timeout = 120
graceful_timeout = 30

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"