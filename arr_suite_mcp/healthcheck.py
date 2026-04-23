"""Container health and readiness checks for the curated Arr MCP server."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import httpx

from .config import ArrSuiteConfig
from .server import ArrSuiteMCPServer

BASE_TOOLS = {
    "arr_execute",
    "arr_explain_intent",
    "arr_list_services",
    "arr_get_system_status",
}

SERVICE_TOOLS = {
    "sonarr": {
        "sonarr_search_series",
        "sonarr_add_series",
        "sonarr_get_series",
        "sonarr_get_calendar",
        "sonarr_get_queue",
        "sonarr_get_history",
        "sonarr_get_root_folders",
        "sonarr_get_quality_profiles",
        "sonarr_get_episodes",
        "sonarr_get_episode",
        "sonarr_update_series",
        "sonarr_delete_queue_item",
        "sonarr_search_episode",
        "sonarr_trigger_command",
        "sonarr_interactive_search",
        "sonarr_grab_release",
        "sonarr_get_custom_formats",
        "sonarr_create_custom_format",
        "sonarr_update_custom_format",
        "sonarr_update_quality_profile",
        "sonarr_list_indexers",
        "sonarr_delete_indexer",
        "sonarr_get_blocklist",
        "sonarr_delete_blocklist_item",
        "sonarr_delete_blocklist_bulk",
        "sonarr_list_release_profiles",
        "sonarr_create_release_profile",
        "sonarr_update_release_profile",
        "sonarr_delete_release_profile",
        "sonarr_get_manual_import_candidates",
        "sonarr_execute_manual_import",
    },
    "radarr": {
        "radarr_search_movie",
        "radarr_add_movie",
        "radarr_get_movies",
        "radarr_get_calendar",
        "radarr_get_queue",
        "radarr_get_history",
        "radarr_get_root_folders",
        "radarr_get_quality_profiles",
        "radarr_get_movie",
        "radarr_lookup_movie",
        "radarr_update_movie",
        "radarr_list_indexers",
        "radarr_delete_indexer",
        "radarr_get_blocklist",
        "radarr_delete_blocklist_item",
        "radarr_delete_blocklist_bulk",
    },
    "prowlarr": {
        "prowlarr_search",
        "prowlarr_get_indexers",
        "prowlarr_get_indexer_schema",
        "prowlarr_add_indexer",
        "prowlarr_update_indexer",
        "prowlarr_delete_indexer",
        "prowlarr_sync_apps",
        "prowlarr_get_applications",
        "prowlarr_get_download_clients",
        "prowlarr_test_indexer",
        "prowlarr_test_all_indexers",
        "prowlarr_get_tags",
        "prowlarr_get_system_health",
    },
}


async def run_healthcheck(health_url: str) -> dict[str, Any]:
    """Run the combined wrapper, registration, and service probe healthcheck."""
    report: dict[str, Any] = {"ok": False, "health_url": health_url}

    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(health_url)
        response.raise_for_status()
        report["wrapper"] = {"ok": True, "status_code": response.status_code}

    server = ArrSuiteMCPServer(ArrSuiteConfig())
    try:
        registered = set(server.get_registered_tool_names())
        expected = set(BASE_TOOLS)
        for service in server.clients:
            expected.update(SERVICE_TOOLS.get(service, set()))

        missing = sorted(expected - registered)
        unexpected = sorted(registered - expected)
        report["tool_registration"] = {
            "ok": not missing and not unexpected,
            "registered_count": len(registered),
            "missing": missing,
            "unexpected": unexpected,
        }
        if missing or unexpected:
            raise RuntimeError("Tool registration mismatch")

        statuses = await server.probe_services()
        report["services"] = statuses
        offline = {name: status for name, status in statuses.items() if not status.get("online")}
        if offline:
            raise RuntimeError(f"Service probe failed for: {', '.join(sorted(offline))}")

        report["ok"] = True
        return report
    finally:
        await server.close()


def _default_health_url() -> str:
    """Return the default local supergateway health endpoint."""
    port = os.getenv("PORT", "8080")
    return f"http://127.0.0.1:{port}/healthz"


def main() -> None:
    """CLI entrypoint for container health checks."""
    parser = argparse.ArgumentParser(description="Arr MCP healthcheck")
    parser.add_argument(
        "--health-url",
        default=_default_health_url(),
        help="Local supergateway health endpoint URL",
    )
    args = parser.parse_args()

    try:
        report = asyncio.run(run_healthcheck(args.health_url))
        print(json.dumps(report, indent=2, default=str))
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001 - CLI healthcheck must return a clear failure
        print(json.dumps({"ok": False, "health_url": args.health_url, "error": str(exc)}, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
