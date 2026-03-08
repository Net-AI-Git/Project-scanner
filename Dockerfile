# Multi-stage build: dependencies first (cache), then app. R1 deployment-and-infrastructure.
# Python 3.12 per pyproject.toml; non-root user; no secrets in image; uvicorn --workers.

FROM python:3.12 AS builder
WORKDIR /app

# Install uv and sync dependencies (layer cache: only re-run when deps change)
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev --no-install-project

# Copy application code
COPY summary_api ./summary_api
COPY pyproject.toml ./

# Install the project in the venv
RUN uv sync --frozen --no-dev

FROM python:3.12
WORKDIR /app

# Copy virtual environment from builder (site-packages usable via PATH)
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Copy application (no .env or secrets)
COPY summary_api ./summary_api
COPY pyproject.toml ./

# Non-root user per R1 security; chown so app can write audit/DLQ under /app
RUN chown -R 1000:1000 /app
USER 1000

EXPOSE 8000

# Production: uvicorn with workers; no --reload. R6 uvicorn-asgi-server.
CMD ["uvicorn", "summary_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
