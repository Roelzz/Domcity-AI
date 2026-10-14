# Domcity-AI — PushPress members API client (MCP server + CLI)
# Built with uv. Serves the MCP endpoint over streamable HTTP on port 8000.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Install dependencies first for layer caching.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

COPY src ./src

# Volume for the persistent token cache.
VOLUME ["/app/data"]

EXPOSE 8000

# Default: serve MCP over HTTP. Override with CMD in Coolify if you prefer stdio.
CMD ["pushpress-mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "8000"]
