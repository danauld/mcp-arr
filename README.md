# mcp-arr

Curated Model Context Protocol server for `Sonarr`, `Radarr`, and `Prowlarr`, built for self-hosted media stacks and deployed as a reproducible Synology/Portainer container.

This first wave intentionally does not chase full upstream parity. It keeps the existing live tool surface, adds a curated Tier 1 expansion, normalizes service errors, and standardizes the build and deployment path around a maintained repository and a published container image.

## Scope

- Runtime target: Synology via Portainer
- Transport: stdio server wrapped by `supergateway`
- HTTP exposure: streamable HTTP with configurable `MCP_HTTP_PATH`
- Supported services in this phase:
  - `Sonarr`
  - `Radarr`
  - `Prowlarr`

## Tool Surface

Base tools:

- `arr_execute`
- `arr_explain_intent`
- `arr_list_services`
- `arr_get_system_status`

Sonarr tools:

- `sonarr_search_series`
- `sonarr_add_series`
- `sonarr_get_series`
- `sonarr_get_calendar`
- `sonarr_get_queue`
- `sonarr_get_history`
- `sonarr_get_root_folders`
- `sonarr_get_quality_profiles`
- `sonarr_get_episodes`
- `sonarr_get_episode`
- `sonarr_update_series`

Radarr tools:

- `radarr_search_movie`
- `radarr_add_movie`
- `radarr_get_movies`
- `radarr_get_calendar`
- `radarr_get_queue`
- `radarr_get_history`
- `radarr_get_root_folders`
- `radarr_get_quality_profiles`
- `radarr_get_movie`
- `radarr_lookup_movie`
- `radarr_update_movie`

Prowlarr tools:

- `prowlarr_search`
- `prowlarr_get_indexers`
- `prowlarr_sync_apps`
- `prowlarr_get_applications`
- `prowlarr_get_download_clients`
- `prowlarr_test_indexer`
- `prowlarr_test_all_indexers`
- `prowlarr_get_tags`
- `prowlarr_get_system_health`

## Environment Contract

Required service configuration:

```bash
SONARR_HOST=sonarr
SONARR_PORT=8989
SONARR_API_KEY=your_sonarr_api_key

RADARR_HOST=radarr
RADARR_PORT=7878
RADARR_API_KEY=your_radarr_api_key

PROWLARR_HOST=prowlarr
PROWLARR_PORT=9696
PROWLARR_API_KEY=your_prowlarr_api_key
```

Global settings:

```bash
LOG_LEVEL=INFO
MCP_HTTP_PATH=/mcp
```

## Local Development

Install the package in editable mode:

```bash
python3 -m pip install -e '.[dev]'
```

Run the stdio server directly:

```bash
arr-mcp
```

Run tests:

```bash
pytest
```

Run the healthcheck locally:

```bash
arr-mcp-healthcheck --health-url http://127.0.0.1:8080/healthz
```

## Container Build

The image build is fully source-based. There is no `git clone` inside the Docker build.

Build locally:

```bash
docker build -f docker/Dockerfile -t mcp-arr:local .
```

The container entrypoint wraps the stdio server with `supergateway` and exposes:

- health endpoint: `/healthz`
- MCP endpoint: `${MCP_HTTP_PATH:-/mcp}`
- listen port: `${PORT:-8080}`

## Portainer Deployment

A checked-in Portainer stack definition lives at:

- `deploy/portainer/docker-compose.yml`

The intended production image is:

- `ghcr.io/danauld/mcp-arr:latest`

Typical rollout:

1. Push the repository to GitHub.
2. Let the `Publish Image` workflow push the image to GHCR.
3. Update the Portainer stack to pull `ghcr.io/danauld/mcp-arr:latest`.
4. Verify `/healthz` and the MCP `tools/list` response.
5. Remove stale duplicate Portainer stack entries only after cutover is clean.

## CI/CD

Included workflows:

- `.github/workflows/ci.yml`
  - installs `.[dev]`
  - runs `compileall`
  - runs `pytest`
- `.github/workflows/publish.yml`
  - builds `docker/Dockerfile`
  - publishes to GHCR on pushes to `main`

## Operational Notes

- `arr_execute` is deliberately restricted to curated search/list/sync flows in this phase.
- Service-specific tools return structured JSON-like payloads wrapped in a normalized MCP response envelope.
- HTTP/service failures are normalized to:
  - `service`
  - `operation`
  - `http_status`
  - `message`
  - `details`
- `prowlarr_sync_apps` uses the `ApplicationIndexerSync` command payload required by the installed Prowlarr v1 API.
