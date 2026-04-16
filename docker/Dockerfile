FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MCP_HTTP_PATH=/mcp \
    PORT=8080 \
    SUPERGATEWAY_LOG_LEVEL=info

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates nodejs npm \
    && npm install -g supergateway@3.4.3 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY arr_suite_mcp ./arr_suite_mcp
RUN python -m pip install --upgrade pip \
    && python -m pip install .

COPY docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -m arr_suite_mcp.healthcheck --health-url "http://127.0.0.1:${PORT:-8080}/healthz" || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
