#!/bin/sh
set -eu

exec supergateway \
  --stdio "arr-mcp" \
  --outputTransport streamableHttp \
  --streamableHttpPath "${MCP_HTTP_PATH:-/mcp}" \
  --healthEndpoint /healthz \
  --port "${PORT:-8080}" \
  --logLevel "${SUPERGATEWAY_LOG_LEVEL:-info}" \
  --cors
