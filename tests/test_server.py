"""Tests for the curated MCP server surface."""

from __future__ import annotations

from typing import Any

import pytest

from arr_suite_mcp.server import ArrSuiteMCPServer


EXPECTED_TOOLS = [
    "arr_execute",
    "arr_explain_intent",
    "arr_list_services",
    "arr_get_system_status",
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
]


class FakeSonarrClient:
    """Minimal fake Sonarr client for update merge tests."""

    def __init__(self) -> None:
        self.updated: dict[str, Any] | None = None

    async def get_series(self, series_id: int) -> dict[str, Any]:
        return {"id": series_id, "title": "Test Series", "monitored": False}

    async def update_series(self, series_data: dict[str, Any]) -> dict[str, Any]:
        self.updated = series_data
        return series_data


class FakeRadarrClient:
    """Minimal fake Radarr client for update merge tests."""

    def __init__(self) -> None:
        self.updated: dict[str, Any] | None = None

    async def get_movie(self, movie_id: int) -> dict[str, Any]:
        return {"id": movie_id, "title": "Test Movie", "monitored": False}

    async def update_movie(self, movie_data: dict[str, Any]) -> dict[str, Any]:
        self.updated = movie_data
        return movie_data

    async def lookup_movie(self, term: str) -> list[dict[str, Any]]:
        return [{"title": term}]


@pytest.fixture
def configured_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure the three supported Arr services."""
    env = {
        "SONARR_HOST": "sonarr",
        "SONARR_PORT": "8989",
        "SONARR_API_KEY": "test-sonarr",
        "RADARR_HOST": "radarr",
        "RADARR_PORT": "7878",
        "RADARR_API_KEY": "test-radarr",
        "PROWLARR_HOST": "prowlarr",
        "PROWLARR_PORT": "9696",
        "PROWLARR_API_KEY": "test-prowlarr",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)


@pytest.mark.asyncio
async def test_registered_tools_match_curated_surface(configured_env: None) -> None:
    """The tool list should exactly match the curated first-wave surface."""
    server = ArrSuiteMCPServer()
    try:
        assert server.get_registered_tool_names() == EXPECTED_TOOLS
    finally:
        await server.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "service"),
    [
        ("sonarr_get_episode", "sonarr"),
        ("radarr_get_movie", "radarr"),
        ("prowlarr_test_indexer", "prowlarr"),
    ],
)
async def test_service_bad_input_returns_normalized_error(
    configured_env: None,
    tool_name: str,
    service: str,
) -> None:
    """Bad tool arguments should stay inside the normalized error envelope."""
    server = ArrSuiteMCPServer()
    try:
        result = await server.dispatch_tool(tool_name, {})
        assert result["ok"] is False
        assert result["service"] == service
        assert result["tool"] == tool_name
        assert result["message"]
        assert isinstance(result["details"], dict)
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_arr_execute_rejects_non_curated_natural_language_flow(
    configured_env: None,
) -> None:
    """Natural-language execution should fail cleanly for unsupported curated actions."""
    server = ArrSuiteMCPServer()
    try:
        result = await server.dispatch_tool(
            "arr_execute",
            {"query": "Add Breaking Bad to my TV shows"},
        )
        assert result["ok"] is False
        assert result["service"] == "sonarr"
        assert "curated" in result["message"].lower()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_arr_execute_routes_generic_search_to_radarr(configured_env: None) -> None:
    """Generic search queries should resolve to the curated Radarr search flow."""
    server = ArrSuiteMCPServer()
    fake = FakeRadarrClient()
    original_client = server.clients["radarr"]
    server.clients["radarr"] = fake
    try:
        result = await server.dispatch_tool(
            "arr_execute",
            {"query": "Search for The Matrix"},
        )
        assert result["ok"] is True
        assert result["data"]["service"] == "radarr"
        assert result["data"]["routed_tool"] == "radarr_search_movie"
        assert result["data"]["result"]["data"] == [{"title": "Search for The Matrix"}]
    finally:
        server.clients["radarr"] = original_client
        await server.close()


@pytest.mark.asyncio
async def test_sonarr_update_series_merges_existing_payload(configured_env: None) -> None:
    """Series updates should merge the current payload before issuing PUT."""
    server = ArrSuiteMCPServer()
    fake = FakeSonarrClient()
    original_client = server.clients["sonarr"]
    server.clients["sonarr"] = fake
    try:
        result = await server.dispatch_tool(
            "sonarr_update_series",
            {"series_id": 42, "fields": {"monitored": True}},
        )
        assert result["ok"] is True
        assert fake.updated == {"id": 42, "title": "Test Series", "monitored": True}
    finally:
        server.clients["sonarr"] = original_client
        await server.close()


@pytest.mark.asyncio
async def test_radarr_update_movie_merges_existing_payload(configured_env: None) -> None:
    """Movie updates should merge the current payload before issuing PUT."""
    server = ArrSuiteMCPServer()
    fake = FakeRadarrClient()
    original_client = server.clients["radarr"]
    server.clients["radarr"] = fake
    try:
        result = await server.dispatch_tool(
            "radarr_update_movie",
            {"movie_id": 24, "fields": {"monitored": True}},
        )
        assert result["ok"] is True
        assert fake.updated == {"id": 24, "title": "Test Movie", "monitored": True}
    finally:
        server.clients["radarr"] = original_client
        await server.close()
