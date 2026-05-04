# ============================================================
# Stage 1 — Builder: install dependencies in a clean layer
# ============================================================
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps needed to compile psycopg2, cryptography, etc.
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ============================================================
# Stage 2 — Runtime: lean production image
# ============================================================
FROM python:3.11-slim AS runtime

# Only the PostgreSQL client lib is needed at runtime
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

# Create a non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY pyproject.toml ./
COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY app/ ./app/
COPY templates/ ./templates/

# Own files by appuser
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run with uvicorn — 4 workers for production
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
