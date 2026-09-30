# Dockerfile — Kanyoza Systems AI Platform
# Single-instance full platform deployment
# Fixed: OpenTelemetry support, Python 3.12 compatibility

FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
# Added: libpq-dev for psycopg2, libxml2-dev for lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    build-essential \
    pkg-config \
    libcairo2-dev \
    libpango1.0-dev \
    libgdk-pixbuf-2.0-dev \
    libffi-dev \
    libssl-dev \
    libxml2-dev \
    libxslt1-dev \
    libpq-dev \
    shared-mime-info \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
# Added: --upgrade pip first
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt --retries 10 --timeout 120

# Copy application
COPY . .

# Create non-root user
RUN useradd -m -u 1000 botuser && chown -R botuser:botuser /app
USER botuser

# Render provides PORT
ENV PORT=10000

# OpenTelemetry configuration (optional)
ENV OTEL_SERVICE_NAME="kanyoza-ai-platform" \
    OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED=true

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=5 \
    CMD curl -f http://localhost:${PORT}/health/live || exit 1

EXPOSE 10000

# Single instance full mode
# Always run API server (all services start via SERVICE_MODE=full)
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000} --timeout-keep-alive 75 --workers 1"]
