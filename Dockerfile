# Dockerfile — Kanyoza Systems AI Platform v14.4
# Multi-service Docker image for zero-budget distributed deployment

FROM python:3.12-slim

WORKDIR /app

# Service mode — defaults to "full", override with --build-arg
ARG SERVICE_MODE=full
ENV SERVICE_MODE=${SERVICE_MODE}

# Render sets PORT env var
ENV PORT=10000

# Install system dependencies (REMOVED libgdk-pixbuf2.0-0 — not available in Trixie)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    build-essential \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt --retries 10 --timeout 120

# Copy application
COPY . .

# Create non-root user
RUN useradd -m -u 1000 botuser && chown -R botuser:botuser /app
USER botuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=5 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

EXPOSE ${PORT}

# Start command depends on SERVICE_MODE
CMD ["sh", "-c", "if [ \"$SERVICE_MODE\" = \"api\" ] || [ \"$SERVICE_MODE\" = \"full\" ]; then gunicorn main:app --worker-class uvicorn.workers.UvicornWorker --workers 1 --threads 4 --bind 0.0.0.0:$PORT --timeout 120; else python worker.py; fi"]
