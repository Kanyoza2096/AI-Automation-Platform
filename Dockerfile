# Dockerfile — Kanyoza Systems AI Platform v14.4.1

FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    build-essential \
    pkg-config \
    libcairo2-dev \
    libpango1.0-dev \
    libgdk-pixbuf-2.0-dev \
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

# Render provides PORT
ENV PORT=10000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=5 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

EXPOSE 10000

# v14.4.1 FIX: No --threads with UvicornWorker (breaks Socket.IO)
# Use uvicorn directly for API mode (best for Socket.IO)
# Use python worker.py for background modes
CMD ["sh", "-c", "if [ \"$SERVICE_MODE\" = \"api\" ] || [ \"$SERVICE_MODE\" = \"full\" ] || [ -z \"$SERVICE_MODE\" ]; then uvicorn main:app --host 0.0.0.0 --port $PORT --timeout-keep-alive 75; else python worker.py; fi"]
