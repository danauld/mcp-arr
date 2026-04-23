"""Main MCP server implementation for the curated Arr service set."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from mcp.server import Server
from mcp.types import TextContent, Tool

from .clients import ArrClientError, ProwlarrClient, RadarrClient, SonarrClient
from .config import ArrSuiteConfig
from .routers import IntentRouter
from .routers.intent_router import ArrService, OperationType

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]

_SECRET_FIELD_NAMES = {"password", "cookie", "apikey", "passkey", "token", "secret"}
_REDACTED = "***REDACTED***"


def _redact_indexer_secrets(obj: Any) -> Any:
    """Walk a Prowlarr indexer/schema structure and redact `fields[].value` where
    the field's name matches a known secret.

    Prowlarr returns indexer configs as `{..., "fields": [{"name": "password", "value": "..."}, ...]}`.
    We redact only values whose accompanying `name` is in the secret list — leaving the
    rest of the structure intact so the caller can still see baseUrl, definitionName, etc.
    """
    if isinstance(obj, list):
        return [_redact_indexer_secrets(x) for x in obj]
    if isinstance(obj, dict):
        result: dict[str, Any] = {}
        for k, v in obj.items():
            if k == "fields" and isinstance(v, list):
                redacted_fields = []
                for f in v:
                    if isinstance(f, dict) and f.get("name") in _SECRET_FIELD_NAMES and "value" in f:
                        redacted_fields.append({**f, "value": _REDACTED})
                    else:
                        redacted_fields.append(_redact_indexer_secrets(f))
                result[k] = redacted_fields
            else:
                result[k] = _redact_indexer_secrets(v)
        return result
    return obj


@dataclass(slots=True)
class ToolSpec:
    """Definition for an MCP tool and its execution metadata."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    service: Optional[str] = None
    operation: Optional[str] = None


@dataclass(slots=True)
class ToolInvocationError(Exception):
    """Structured error raised by server-side tool helpers."""

    message: str
    service: Optional[str] = None
    operation: Optional[str] = None
    http_status: Optional[int] = None
    details: Optional[dict[str, Any]] = None

    def __str__(self) -> str:
        return self.message


class ArrSuiteMCPServer:
    """Curated MCP server for Sonarr, Radarr, and Prowlarr."""

    SUPPORTED_SERVICES = ("sonarr", "radarr", "prowlarr")

    def __init__(self, config: Optional[ArrSuiteConfig] = None):
        """Initialize the MCP server."""
        self.config = config or ArrSuiteConfig()
        self.server = Server("mcp-arr")
        self.router = IntentRouter()
        self.clients: dict[str, Any] = {}
        self.tool_specs: dict[str, ToolSpec] = {}

        self._initialize_clients()
        self._register_tool_specs()
        self._register_handlers()

        logger.info("Enabled services: %s", ", ".join(self.clients) or "none")
        logger.info("Registered tools: %s", ", ".join(self.get_registered_tool_names()))

    def _initialize_clients(self) -> None:
        """Initialize API clients for the supported services."""
        if self.config.sonarr and self.config.sonarr.api_key:
            self.clients["sonarr"] = SonarrClient(
                base_url=self.config.sonarr.base_url,
                api_key=self.config.sonarr.api_key,
                timeout=self.config.request_timeout,
                max_retries=self.config.max_retries,
            )

        if self.config.radarr and self.config.radarr.api_key:
            self.clients["radarr"] = RadarrClient(
                base_url=self.config.radarr.base_url,
                api_key=self.config.radarr.api_key,
                timeout=self.config.request_timeout,
                max_retries=self.config.max_retries,
            )

        if self.config.prowlarr and self.config.prowlarr.api_key:
            self.clients["prowlarr"] = ProwlarrClient(
                base_url=self.config.prowlarr.base_url,
                api_key=self.config.prowlarr.api_key,
                timeout=self.config.request_timeout,
                max_retries=self.config.max_retries,
            )

    def _register_tool_specs(self) -> None:
        """Register the curated tool surface exposed by the server."""
        self._add_tool(
            "arr_execute",
            (
                "Execute a supported Arr operation using natural language. "
                "Routes only within the curated Sonarr, Radarr, and Prowlarr tool surface."
            ),
            {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language query describing what you want to do",
                    }
                },
                "required": ["query"],
            },
            self._tool_arr_execute,
        )
        self._add_tool(
            "arr_explain_intent",
            "Explain how a natural language query would be interpreted and which curated tool it maps to.",
            {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language query to explain",
                    }
                },
                "required": ["query"],
            },
            self._tool_arr_explain_intent,
        )
        self._add_tool(
            "arr_list_services",
            "List the supported Arr services and whether each one is configured.",
            {"type": "object", "properties": {}},
            self._tool_arr_list_services,
        )
        self._add_tool(
            "arr_get_system_status",
            "Get system status for each configured Arr service.",
            {"type": "object", "properties": {}},
            self._tool_arr_get_system_status,
        )

        if "sonarr" in self.clients:
            self._register_sonarr_tools()
        if "radarr" in self.clients:
            self._register_radarr_tools()
        if "prowlarr" in self.clients:
            self._register_prowlarr_tools()

    def _register_sonarr_tools(self) -> None:
        """Register Sonarr tools."""
        self._add_tool(
            "sonarr_search_series",
            "Search for TV series in Sonarr.",
            {
                "type": "object",
                "properties": {"term": {"type": "string", "description": "Search term"}},
                "required": ["term"],
            },
            self._tool_sonarr_search_series,
            service="sonarr",
            operation="lookup_series",
        )
        self._add_tool(
            "sonarr_add_series",
            "Add a new TV series to Sonarr.",
            {
                "type": "object",
                "properties": {
                    "tvdb_id": {"type": "integer", "description": "TVDB ID"},
                    "quality_profile_id": {
                        "type": "integer",
                        "description": "Quality profile ID",
                    },
                    "root_folder_path": {
                        "type": "string",
                        "description": "Root folder path",
                    },
                    "monitored": {
                        "type": "boolean",
                        "description": "Monitor series",
                        "default": True,
                    },
                },
                "required": ["tvdb_id", "quality_profile_id", "root_folder_path"],
            },
            self._tool_sonarr_add_series,
            service="sonarr",
            operation="add_series",
        )
        self._add_tool(
            "sonarr_get_series",
            "Get all series or a specific series.",
            {
                "type": "object",
                "properties": {
                    "series_id": {
                        "type": "integer",
                        "description": "Optional series ID",
                    }
                },
            },
            self._tool_sonarr_get_series,
            service="sonarr",
            operation="get_series",
        )
        self._add_tool(
            "sonarr_get_calendar",
            "Get upcoming episodes from Sonarr's calendar.",
            {
                "type": "object",
                "properties": {
                    "start": {"type": "string", "description": "Optional ISO start date"},
                    "end": {"type": "string", "description": "Optional ISO end date"},
                },
            },
            self._tool_sonarr_get_calendar,
            service="sonarr",
            operation="get_calendar",
        )
        self._add_tool(
            "sonarr_get_queue",
            "Get Sonarr's download queue.",
            {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "default": 1},
                    "page_size": {"type": "integer", "default": 20},
                    "include_unknown_series": {"type": "boolean", "default": False},
                },
            },
            self._tool_sonarr_get_queue,
            service="sonarr",
            operation="get_queue",
        )
        self._add_tool(
            "sonarr_get_history",
            "Get Sonarr download/import history.",
            {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "default": 1},
                    "page_size": {"type": "integer", "default": 20},
                    "series_id": {"type": "integer"},
                    "event_type": {"type": "string"},
                },
            },
            self._tool_sonarr_get_history,
            service="sonarr",
            operation="get_history",
        )
        self._add_tool(
            "sonarr_get_root_folders",
            "Get Sonarr root folders.",
            {"type": "object", "properties": {}},
            self._tool_sonarr_get_root_folders,
            service="sonarr",
            operation="get_root_folders",
        )
        self._add_tool(
            "sonarr_get_quality_profiles",
            "Get Sonarr quality profiles.",
            {"type": "object", "properties": {}},
            self._tool_sonarr_get_quality_profiles,
            service="sonarr",
            operation="get_quality_profiles",
        )
        self._add_tool(
            "sonarr_get_episodes",
            "Get episodes for a specific Sonarr series.",
            {
                "type": "object",
                "properties": {
                    "series_id": {"type": "integer", "description": "Series ID"}
                },
                "required": ["series_id"],
            },
            self._tool_sonarr_get_episodes,
            service="sonarr",
            operation="get_episodes",
        )
        self._add_tool(
            "sonarr_get_episode",
            "Get a specific Sonarr episode by ID.",
            {
                "type": "object",
                "properties": {
                    "episode_id": {"type": "integer", "description": "Episode ID"}
                },
                "required": ["episode_id"],
            },
            self._tool_sonarr_get_episode,
            service="sonarr",
            operation="get_episode",
        )
        self._add_tool(
            "sonarr_update_series",
            "Update a Sonarr series by merging the provided fields into the current series payload.",
            {
                "type": "object",
                "properties": {
                    "series_id": {"type": "integer", "description": "Series ID"},
                    "fields": {
                        "type": "object",
                        "description": "Field values to merge into the current series payload",
                    },
                },
                "required": ["series_id", "fields"],
            },
            self._tool_sonarr_update_series,
            service="sonarr",
            operation="update_series",
        )

        # --- Queue management ---------------------------------------------------
        self._add_tool(
            "sonarr_delete_queue_item",
            "Delete a single queued download in Sonarr. Setting remove_from_client=true also removes it from qBittorrent/the download client; blocklist=true prevents the same release being grabbed again. This is the one-call version of 'kill a stuck French torrent and blocklist it so it doesn't come back'.",
            {
                "type": "object",
                "properties": {
                    "queue_id": {"type": "integer", "description": "Queue item ID (from sonarr_get_queue)"},
                    "remove_from_client": {"type": "boolean", "default": True, "description": "Also remove from the download client (qBittorrent etc.)"},
                    "blocklist": {"type": "boolean", "default": False, "description": "Blocklist the release so Sonarr won't grab it again"},
                },
                "required": ["queue_id"],
            },
            self._tool_sonarr_delete_queue_item,
            service="sonarr",
            operation="delete_queue_item",
        )

        # --- Episode / series search (command trigger) --------------------------
        self._add_tool(
            "sonarr_search_episode",
            "Force a search for a single episode by episode id. Triggers the EpisodeSearch command.",
            {
                "type": "object",
                "properties": {
                    "episode_id": {"type": "integer", "description": "Episode ID"},
                },
                "required": ["episode_id"],
            },
            self._tool_sonarr_search_episode,
            service="sonarr",
            operation="search_episode",
        )

        self._add_tool(
            "sonarr_trigger_command",
            "Generic Sonarr command trigger. Covers EpisodeSearch, SeasonSearch, SeriesSearch, RefreshSeries, RescanSeries, RenameSeries, RssSync, MissingEpisodeSearch, CutoffUnmetEpisodeSearch, Backup, and others. Pass the command name and any command-specific fields in `payload`.",
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Sonarr command name (e.g. 'EpisodeSearch', 'RssSync', 'RefreshSeries')"},
                    "payload": {"type": "object", "description": "Extra fields to include on the command body (e.g. {episodeIds: [1,2]}, {seriesId: 10}, {seriesIds: [10]}).", "default": {}},
                },
                "required": ["name"],
            },
            self._tool_sonarr_trigger_command,
            service="sonarr",
            operation="trigger_command",
        )

        # --- Interactive release search + manual grab ---------------------------
        self._add_tool(
            "sonarr_interactive_search",
            "List all releases Sonarr's indexers can currently find for an episode, season, or series — the same data the UI's interactive-search grid shows. Results include score, quality, rejection reasons, guid, and indexer id. Feed a chosen row's {guid, indexer_id} to sonarr_grab_release to actually download it.",
            {
                "type": "object",
                "properties": {
                    "episode_id": {"type": "integer", "description": "Episode ID to search for"},
                    "series_id": {"type": "integer", "description": "Series ID (use with season_number, or alone for full-series search)"},
                    "season_number": {"type": "integer", "description": "Season number (requires series_id)"},
                },
            },
            self._tool_sonarr_interactive_search,
            service="sonarr",
            operation="interactive_search",
        )

        self._add_tool(
            "sonarr_grab_release",
            "Manually grab a specific release (push it to the download client). Use after sonarr_interactive_search picks the row you want. Set should_override=true to force-map the release to specific episodes/season/quality/languages when Sonarr's parser guessed wrong (e.g. SpaZe AEW releases whose `342` is parsed as S3E42). Language ids: English=1, French=2, Spanish=3, German=4, Italian=5, Danish=6, Dutch=7, Japanese=8 (see Sonarr's internal language table for others).",
            {
                "type": "object",
                "properties": {
                    "guid": {"type": "string", "description": "Release guid from sonarr_interactive_search"},
                    "indexer_id": {"type": "integer", "description": "Indexer id from the same release row"},
                    "should_override": {"type": "boolean", "default": False, "description": "If true, apply the override fields below (mirrors the UI's 'Override and add to Download Queue' action)"},
                    "episode_ids": {"type": "array", "items": {"type": "integer"}, "description": "Force-map to these episode ids (use sonarr_get_episodes to look them up)"},
                    "season_number": {"type": "integer", "description": "Season number — used for season-pack grabs"},
                    "series_id": {"type": "integer", "description": "Series id — for rare cross-series overrides"},
                    "quality": {"type": "object", "description": "Quality object, e.g. {quality: {id: 3}, revision: {version: 1, real: 0, isRepack: false}}"},
                    "languages": {"type": "array", "items": {"type": "object"}, "description": "Language objects, e.g. [{id: 1}] for English"},
                },
                "required": ["guid", "indexer_id"],
            },
            self._tool_sonarr_grab_release,
            service="sonarr",
            operation="grab_release",
        )

        # --- Custom Formats -----------------------------------------------------
        self._add_tool(
            "sonarr_get_custom_formats",
            "List all configured custom formats with their specifications.",
            {"type": "object", "properties": {}},
            self._tool_sonarr_get_custom_formats,
            service="sonarr",
            operation="get_custom_formats",
        )

        self._add_tool(
            "sonarr_create_custom_format",
            "Create a new custom format. Pass the full Sonarr v3 payload (name, includeCustomFormatWhenRenaming, specifications[]).",
            {
                "type": "object",
                "properties": {
                    "payload": {
                        "type": "object",
                        "description": "Full custom format payload per Sonarr v3 /customformat POST schema",
                    },
                },
                "required": ["payload"],
            },
            self._tool_sonarr_create_custom_format,
            service="sonarr",
            operation="create_custom_format",
        )

        self._add_tool(
            "sonarr_update_custom_format",
            "Update an existing custom format. Typical flow: sonarr_get_custom_formats → pick one → modify → pass full object here.",
            {
                "type": "object",
                "properties": {
                    "custom_format_id": {"type": "integer", "description": "Custom format id"},
                    "payload": {"type": "object", "description": "Full custom format object (merge your edits into the current shape)"},
                },
                "required": ["custom_format_id", "payload"],
            },
            self._tool_sonarr_update_custom_format,
            service="sonarr",
            operation="update_custom_format",
        )

        # --- Quality Profile modification --------------------------------------
        self._add_tool(
            "sonarr_update_quality_profile",
            "Update a quality profile — used for adjusting format scores, cutoff, minFormatScore, language priorities. Typical flow: sonarr_get_quality_profiles → copy the profile → modify formatItems/cutoff → pass full object here.",
            {
                "type": "object",
                "properties": {
                    "profile_id": {"type": "integer", "description": "Quality profile id"},
                    "payload": {"type": "object", "description": "Full quality profile object with your edits merged in"},
                },
                "required": ["profile_id", "payload"],
            },
            self._tool_sonarr_update_quality_profile,
            service="sonarr",
            operation="update_quality_profile",
        )

        # --- Indexers ----------------------------------------------------------
        self._add_tool(
            "sonarr_list_indexers",
            "List all indexers configured in Sonarr. Includes Prowlarr-synced entries (names suffixed '(Prowlarr)') and any standalone indexers added directly (raw RSS, custom Newznab, etc.).",
            {"type": "object", "properties": {}},
            self._tool_sonarr_list_indexers,
            service="sonarr",
            operation="list_indexers",
        )

        self._add_tool(
            "sonarr_delete_indexer",
            "Delete an indexer from Sonarr by id. Prowlarr-synced indexers will be re-added on next sync unless removed from Prowlarr first.",
            {
                "type": "object",
                "properties": {
                    "indexer_id": {"type": "integer", "description": "Sonarr indexer id"},
                },
                "required": ["indexer_id"],
            },
            self._tool_sonarr_delete_indexer,
            service="sonarr",
            operation="delete_indexer",
        )

        # --- Blocklist ---------------------------------------------------------
        self._add_tool(
            "sonarr_get_blocklist",
            "List blocklisted releases, paginated.",
            {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "default": 1},
                    "page_size": {"type": "integer", "default": 20},
                    "sort_key": {"type": "string", "default": "date"},
                    "sort_direction": {"type": "string", "default": "descending"},
                },
            },
            self._tool_sonarr_get_blocklist,
            service="sonarr",
            operation="get_blocklist",
        )

        self._add_tool(
            "sonarr_delete_blocklist_item",
            "Remove a single entry from Sonarr's blocklist by id. Use to undo an accidental blocklist during queue cleanup.",
            {
                "type": "object",
                "properties": {
                    "blocklist_id": {"type": "integer"},
                },
                "required": ["blocklist_id"],
            },
            self._tool_sonarr_delete_blocklist_item,
            service="sonarr",
            operation="delete_blocklist_item",
        )

        self._add_tool(
            "sonarr_delete_blocklist_bulk",
            "Remove multiple entries from Sonarr's blocklist by id. Pass ids=[] to clear the entire blocklist.",
            {
                "type": "object",
                "properties": {
                    "blocklist_ids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["blocklist_ids"],
            },
            self._tool_sonarr_delete_blocklist_bulk,
            service="sonarr",
            operation="delete_blocklist_bulk",
        )

        # --- Release Profiles --------------------------------------------------
        self._add_tool(
            "sonarr_list_release_profiles",
            "List all release profiles (preferred/ignored/required terms, indexer filters, tag scopes).",
            {"type": "object", "properties": {}},
            self._tool_sonarr_list_release_profiles,
            service="sonarr",
            operation="list_release_profiles",
        )

        self._add_tool(
            "sonarr_create_release_profile",
            "Create a release profile. Payload shape: {name, enabled, required:[], ignored:[], preferred:[{term, score}], includePreferredWhenRenaming, indexerId (0=all), tags:[]}. Use to implement 'prefer HEEL over SpaZe' style rules.",
            {
                "type": "object",
                "properties": {
                    "payload": {"type": "object", "description": "Release profile payload per Sonarr v3 /releaseprofile POST schema"},
                },
                "required": ["payload"],
            },
            self._tool_sonarr_create_release_profile,
            service="sonarr",
            operation="create_release_profile",
        )

        self._add_tool(
            "sonarr_update_release_profile",
            "Update an existing release profile. Typical flow: sonarr_list_release_profiles → pick one → modify → pass the full object here.",
            {
                "type": "object",
                "properties": {
                    "profile_id": {"type": "integer"},
                    "payload": {"type": "object"},
                },
                "required": ["profile_id", "payload"],
            },
            self._tool_sonarr_update_release_profile,
            service="sonarr",
            operation="update_release_profile",
        )

        self._add_tool(
            "sonarr_delete_release_profile",
            "Delete a release profile by id.",
            {
                "type": "object",
                "properties": {"profile_id": {"type": "integer"}},
                "required": ["profile_id"],
            },
            self._tool_sonarr_delete_release_profile,
            service="sonarr",
            operation="delete_release_profile",
        )

        # --- Manual import -----------------------------------------------------
        self._add_tool(
            "sonarr_get_manual_import_candidates",
            "List import candidates for a folder or download. Returns files with Sonarr's best-guess episode mapping, quality, language, and rejection reasons — review before calling sonarr_execute_manual_import.",
            {
                "type": "object",
                "properties": {
                    "folder": {"type": "string", "description": "Absolute folder path"},
                    "download_id": {"type": "string", "description": "Download client id"},
                    "series_id": {"type": "integer"},
                    "filter_existing_files": {"type": "boolean", "default": True},
                },
            },
            self._tool_sonarr_get_manual_import_candidates,
            service="sonarr",
            operation="get_manual_import_candidates",
        )

        self._add_tool(
            "sonarr_execute_manual_import",
            "Import specific files into the library. Each file entry must include {path, seriesId, episodeIds, quality, languages}. import_mode is 'auto' | 'move' | 'copy'.",
            {
                "type": "object",
                "properties": {
                    "files": {"type": "array", "items": {"type": "object"}},
                    "import_mode": {"type": "string", "default": "auto"},
                },
                "required": ["files"],
            },
            self._tool_sonarr_execute_manual_import,
            service="sonarr",
            operation="execute_manual_import",
        )

    def _register_radarr_tools(self) -> None:
        """Register Radarr tools."""
        self._add_tool(
            "radarr_search_movie",
            "Search for movies in Radarr.",
            {
                "type": "object",
                "properties": {"term": {"type": "string", "description": "Search term"}},
                "required": ["term"],
            },
            self._tool_radarr_search_movie,
            service="radarr",
            operation="lookup_movie",
        )
        self._add_tool(
            "radarr_add_movie",
            "Add a new movie to Radarr.",
            {
                "type": "object",
                "properties": {
                    "tmdb_id": {"type": "integer", "description": "TMDB ID"},
                    "quality_profile_id": {
                        "type": "integer",
                        "description": "Quality profile ID",
                    },
                    "root_folder_path": {
                        "type": "string",
                        "description": "Root folder path",
                    },
                    "monitored": {
                        "type": "boolean",
                        "description": "Monitor movie",
                        "default": True,
                    },
                },
                "required": ["tmdb_id", "quality_profile_id", "root_folder_path"],
            },
            self._tool_radarr_add_movie,
            service="radarr",
            operation="add_movie",
        )
        self._add_tool(
            "radarr_get_movies",
            "Get all movies or a specific movie.",
            {
                "type": "object",
                "properties": {
                    "movie_id": {
                        "type": "integer",
                        "description": "Optional movie ID",
                    }
                },
            },
            self._tool_radarr_get_movies,
            service="radarr",
            operation="get_movies",
        )
        self._add_tool(
            "radarr_get_calendar",
            "Get upcoming movie releases from Radarr's calendar.",
            {
                "type": "object",
                "properties": {
                    "start": {"type": "string", "description": "Optional ISO start date"},
                    "end": {"type": "string", "description": "Optional ISO end date"},
                },
            },
            self._tool_radarr_get_calendar,
            service="radarr",
            operation="get_calendar",
        )
        self._add_tool(
            "radarr_get_queue",
            "Get Radarr's download queue.",
            {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "default": 1},
                    "page_size": {"type": "integer", "default": 20},
                    "include_unknown_movies": {"type": "boolean", "default": False},
                },
            },
            self._tool_radarr_get_queue,
            service="radarr",
            operation="get_queue",
        )
        self._add_tool(
            "radarr_get_history",
            "Get Radarr download/import history.",
            {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "default": 1},
                    "page_size": {"type": "integer", "default": 20},
                    "movie_id": {"type": "integer"},
                    "event_type": {"type": "string"},
                },
            },
            self._tool_radarr_get_history,
            service="radarr",
            operation="get_history",
        )
        self._add_tool(
            "radarr_get_root_folders",
            "Get Radarr root folders.",
            {"type": "object", "properties": {}},
            self._tool_radarr_get_root_folders,
            service="radarr",
            operation="get_root_folders",
        )
        self._add_tool(
            "radarr_get_quality_profiles",
            "Get Radarr quality profiles.",
            {"type": "object", "properties": {}},
            self._tool_radarr_get_quality_profiles,
            service="radarr",
            operation="get_quality_profiles",
        )
        self._add_tool(
            "radarr_get_movie",
            "Get a specific Radarr movie by ID.",
            {
                "type": "object",
                "properties": {"movie_id": {"type": "integer", "description": "Movie ID"}},
                "required": ["movie_id"],
            },
            self._tool_radarr_get_movie,
            service="radarr",
            operation="get_movie",
        )
        self._add_tool(
            "radarr_lookup_movie",
            "Look up a movie in Radarr without adding it.",
            {
                "type": "object",
                "properties": {"term": {"type": "string", "description": "Search term"}},
                "required": ["term"],
            },
            self._tool_radarr_lookup_movie,
            service="radarr",
            operation="lookup_movie",
        )
        self._add_tool(
            "radarr_update_movie",
            "Update a Radarr movie by merging the provided fields into the current movie payload.",
            {
                "type": "object",
                "properties": {
                    "movie_id": {"type": "integer", "description": "Movie ID"},
                    "fields": {
                        "type": "object",
                        "description": "Field values to merge into the current movie payload",
                    },
                },
                "required": ["movie_id", "fields"],
            },
            self._tool_radarr_update_movie,
            service="radarr",
            operation="update_movie",
        )

        # --- Indexers ----------------------------------------------------------
        self._add_tool(
            "radarr_list_indexers",
            "List all indexers configured in Radarr (includes Prowlarr-synced entries).",
            {"type": "object", "properties": {}},
            self._tool_radarr_list_indexers,
            service="radarr",
            operation="list_indexers",
        )
        self._add_tool(
            "radarr_delete_indexer",
            "Delete an indexer from Radarr by id. Prowlarr-synced indexers re-appear on next sync unless removed from Prowlarr first.",
            {
                "type": "object",
                "properties": {"indexer_id": {"type": "integer"}},
                "required": ["indexer_id"],
            },
            self._tool_radarr_delete_indexer,
            service="radarr",
            operation="delete_indexer",
        )

        # --- Blocklist ---------------------------------------------------------
        self._add_tool(
            "radarr_get_blocklist",
            "List blocklisted movie releases, paginated.",
            {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "default": 1},
                    "page_size": {"type": "integer", "default": 20},
                    "sort_key": {"type": "string", "default": "date"},
                    "sort_direction": {"type": "string", "default": "descending"},
                },
            },
            self._tool_radarr_get_blocklist,
            service="radarr",
            operation="get_blocklist",
        )
        self._add_tool(
            "radarr_delete_blocklist_item",
            "Remove a single entry from Radarr's blocklist by id.",
            {
                "type": "object",
                "properties": {"blocklist_id": {"type": "integer"}},
                "required": ["blocklist_id"],
            },
            self._tool_radarr_delete_blocklist_item,
            service="radarr",
            operation="delete_blocklist_item",
        )
        self._add_tool(
            "radarr_delete_blocklist_bulk",
            "Remove multiple entries from Radarr's blocklist. Pass ids=[] to clear the blocklist.",
            {
                "type": "object",
                "properties": {
                    "blocklist_ids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["blocklist_ids"],
            },
            self._tool_radarr_delete_blocklist_bulk,
            service="radarr",
            operation="delete_blocklist_bulk",
        )

    def _register_prowlarr_tools(self) -> None:
        """Register Prowlarr tools."""
        self._add_tool(
            "prowlarr_search",
            "Search for releases across all Prowlarr indexers.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "type": {
                        "type": "string",
                        "description": "Search type (search, tvsearch, movie)",
                        "default": "search",
                    },
                },
                "required": ["query"],
            },
            self._tool_prowlarr_search,
            service="prowlarr",
            operation="search",
        )
        self._add_tool(
            "prowlarr_get_indexers",
            "List all configured Prowlarr indexers. Secret field values (password, cookie, apikey, passkey, token, secret) are redacted by default — pass include_secrets=true to see them raw.",
            {
                "type": "object",
                "properties": {
                    "include_secrets": {
                        "type": "boolean",
                        "default": False,
                        "description": "If true, return credential fields unredacted",
                    },
                },
            },
            self._tool_prowlarr_get_indexers,
            service="prowlarr",
            operation="get_indexers",
        )
        self._add_tool(
            "prowlarr_get_indexer_schema",
            "Get indexer definition templates (Cardigann/Newznab/Torznab). Required to build an add_indexer payload. Pass definition_name (e.g. 'xwtorrents') to filter; unfiltered response can be 600+ entries.",
            {
                "type": "object",
                "properties": {
                    "definition_name": {"type": "string", "description": "Filter to this indexer, e.g. 'xwtorrents'"},
                    "include_secrets": {"type": "boolean", "default": False},
                },
            },
            self._tool_prowlarr_get_indexer_schema,
            service="prowlarr",
            operation="get_indexer_schema",
        )
        self._add_tool(
            "prowlarr_add_indexer",
            "Add a new indexer to Prowlarr. Use prowlarr_get_indexer_schema(definition_name) first to get the template, fill in fields (credentials, baseUrl, etc.), then pass the full payload here.",
            {
                "type": "object",
                "properties": {
                    "payload": {"type": "object", "description": "Full indexer payload"},
                },
                "required": ["payload"],
            },
            self._tool_prowlarr_add_indexer,
            service="prowlarr",
            operation="add_indexer",
        )
        self._add_tool(
            "prowlarr_update_indexer",
            "Update an existing Prowlarr indexer. Typical flow: prowlarr_get_indexers(include_secrets=true) → pick one → modify → pass full object.",
            {
                "type": "object",
                "properties": {
                    "indexer_id": {"type": "integer"},
                    "payload": {"type": "object"},
                },
                "required": ["indexer_id", "payload"],
            },
            self._tool_prowlarr_update_indexer,
            service="prowlarr",
            operation="update_indexer",
        )
        self._add_tool(
            "prowlarr_delete_indexer",
            "Delete a Prowlarr indexer by id. This also removes it from synced downstream apps (Sonarr/Radarr) on their next sync.",
            {
                "type": "object",
                "properties": {"indexer_id": {"type": "integer"}},
                "required": ["indexer_id"],
            },
            self._tool_prowlarr_delete_indexer,
            service="prowlarr",
            operation="delete_indexer",
        )
        self._add_tool(
            "prowlarr_sync_apps",
            "Sync indexers to all connected applications in Prowlarr.",
            {"type": "object", "properties": {}},
            self._tool_prowlarr_sync_apps,
            service="prowlarr",
            operation="sync_apps",
        )
        self._add_tool(
            "prowlarr_get_applications",
            "Get all connected applications from Prowlarr.",
            {"type": "object", "properties": {}},
            self._tool_prowlarr_get_applications,
            service="prowlarr",
            operation="get_applications",
        )
        self._add_tool(
            "prowlarr_get_download_clients",
            "Get all configured download clients from Prowlarr.",
            {"type": "object", "properties": {}},
            self._tool_prowlarr_get_download_clients,
            service="prowlarr",
            operation="get_download_clients",
        )
        self._add_tool(
            "prowlarr_test_indexer",
            "Test a specific Prowlarr indexer by ID.",
            {
                "type": "object",
                "properties": {
                    "indexer_id": {"type": "integer", "description": "Indexer ID"}
                },
                "required": ["indexer_id"],
            },
            self._tool_prowlarr_test_indexer,
            service="prowlarr",
            operation="test_indexer",
        )
        self._add_tool(
            "prowlarr_test_all_indexers",
            "Test all configured Prowlarr indexers.",
            {"type": "object", "properties": {}},
            self._tool_prowlarr_test_all_indexers,
            service="prowlarr",
            operation="test_all_indexers",
        )
        self._add_tool(
            "prowlarr_get_tags",
            "Get all Prowlarr tags.",
            {"type": "object", "properties": {}},
            self._tool_prowlarr_get_tags,
            service="prowlarr",
            operation="get_tags",
        )
        self._add_tool(
            "prowlarr_get_system_health",
            "Get Prowlarr health warnings and system health details.",
            {"type": "object", "properties": {}},
            self._tool_prowlarr_get_system_health,
            service="prowlarr",
            operation="get_system_health",
        )

    def _add_tool(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: ToolHandler,
        *,
        service: Optional[str] = None,
        operation: Optional[str] = None,
    ) -> None:
        """Register a tool specification."""
        self.tool_specs[name] = ToolSpec(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
            service=service,
            operation=operation,
        )

    def _register_handlers(self) -> None:
        """Register MCP protocol handlers."""

        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            return [
                Tool(
                    name=spec.name,
                    description=spec.description,
                    inputSchema=spec.input_schema,
                )
                for spec in self.tool_specs.values()
            ]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: Optional[dict[str, Any]]) -> list[TextContent]:
            payload = await self.dispatch_tool(name, arguments or {})
            return [TextContent(type="text", text=self._serialize_payload(payload))]

    async def dispatch_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a tool call and always return a structured payload."""
        spec = self.tool_specs.get(name)
        if spec is None:
            return self._error_payload(
                service=None,
                operation=name,
                message=f"Unknown tool: {name}",
                details={"available_tools": self.get_registered_tool_names()},
            )

        try:
            result = await spec.handler(arguments)
            return {
                "ok": True,
                "tool": name,
                "service": spec.service,
                "operation": spec.operation,
                "data": result,
            }
        except ArrClientError as exc:
            return self._error_payload(
                service=spec.service,
                operation=spec.operation or name,
                message=exc.message,
                http_status=exc.http_status,
                details=exc.details,
                tool=name,
            )
        except ToolInvocationError as exc:
            return self._error_payload(
                service=exc.service or spec.service,
                operation=exc.operation or spec.operation or name,
                message=exc.message,
                http_status=exc.http_status,
                details=exc.details,
                tool=name,
            )
        except Exception as exc:  # noqa: BLE001 - normalized for MCP response
            logger.error("Error handling tool %s", name, exc_info=True)
            return self._error_payload(
                service=spec.service,
                operation=spec.operation or name,
                message=str(exc),
                details={"arguments": arguments},
                tool=name,
            )

    def get_registered_tool_names(self) -> list[str]:
        """Return the registered tool names in declaration order."""
        return list(self.tool_specs.keys())

    async def probe_services(self) -> dict[str, dict[str, Any]]:
        """Probe each configured Arr service and return health details."""
        statuses: dict[str, dict[str, Any]] = {}
        for name, client in self.clients.items():
            try:
                status = await client.get_system_status()
                statuses[name] = {"online": True, "status": status}
            except ArrClientError as exc:
                statuses[name] = {
                    "online": False,
                    "service": name,
                    "http_status": exc.http_status,
                    "message": exc.message,
                    "details": exc.details,
                }
            except Exception as exc:  # noqa: BLE001 - health summary should not crash the server
                statuses[name] = {
                    "online": False,
                    "service": name,
                    "message": str(exc),
                    "details": {},
                }
        return statuses

    async def close(self) -> None:
        """Close all initialized API clients."""
        for client in self.clients.values():
            await client.close()

    async def _tool_arr_execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a curated Arr operation from natural language."""
        query = self._require_string(arguments, "query")
        service, operation, context = self._resolve_curated_intent(query)
        routed = self._route_intent_to_tool(service, operation)

        if service.value not in self.clients:
            raise ToolInvocationError(
                service=service.value,
                operation=operation.value,
                message=f"{service.value.capitalize()} is not configured",
                details={"available_services": list(self.clients.keys())},
            )

        if routed is None:
            raise ToolInvocationError(
                service=service.value,
                operation=operation.value,
                message=(
                    "Natural language execution only supports the curated search, list, and sync flows "
                    "for this phase. Use a service-specific tool for the full operation."
                ),
                details={
                    "intent_context": context,
                    "supported_tools": self.get_registered_tool_names(),
                },
            )

        tool_name, routed_arguments = self._arguments_for_routed_tool(routed, query, context)
        result = await self.dispatch_tool(tool_name, routed_arguments)
        if not result.get("ok", False):
            raise ToolInvocationError(
                service=result.get("service") or service.value,
                operation=result.get("operation") or operation.value,
                http_status=result.get("http_status"),
                message=result.get("message", "Natural language execution failed"),
                details={
                    "query": query,
                    "intent_context": context,
                    "routed_tool": tool_name,
                    "routed_result": result,
                },
            )
        return {
            "query": query,
            "service": service.value,
            "operation": operation.value,
            "intent_context": context,
            "routed_tool": tool_name,
            "result": result,
        }

    async def _tool_arr_explain_intent(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Explain how the natural language query would be handled."""
        query = self._require_string(arguments, "query")
        raw_service, raw_operation, raw_context = self.router.route(query)
        service, operation, context = self._resolve_curated_intent(query)
        routed = self._route_intent_to_tool(service, operation)
        return {
            "query": query,
            "service": service.value,
            "operation": operation.value,
            "intent_context": context,
            "raw_router_service": raw_service.value,
            "raw_router_operation": raw_operation.value,
            "raw_router_context": raw_context,
            "routed_tool": routed,
            "supported": routed is not None,
            "explanation": self.router.explain_intent(query),
        }

    async def _tool_arr_list_services(self, _: dict[str, Any]) -> dict[str, Any]:
        """List supported services and their configuration state."""
        service_configs = {
            "sonarr": self.config.sonarr,
            "radarr": self.config.radarr,
            "prowlarr": self.config.prowlarr,
        }
        return {
            "enabled_services": list(self.clients.keys()),
            "services": {
                name: {
                    "configured": name in self.clients,
                    "base_url": config.base_url if config else None,
                }
                for name, config in service_configs.items()
            },
            "registered_tools": self.get_registered_tool_names(),
        }

    async def _tool_arr_get_system_status(self, _: dict[str, Any]) -> dict[str, Any]:
        """Return the current health and registration summary."""
        return {
            "enabled_services": list(self.clients.keys()),
            "tool_count": len(self.tool_specs),
            "registered_tools": self.get_registered_tool_names(),
            "service_status": await self.probe_services(),
        }

    async def _tool_sonarr_search_series(self, arguments: dict[str, Any]) -> Any:
        return await self._get_sonarr().lookup_series(self._require_string(arguments, "term"))

    async def _tool_sonarr_add_series(self, arguments: dict[str, Any]) -> Any:
        return await self._get_sonarr().add_series(**arguments)

    async def _tool_sonarr_get_series(self, arguments: dict[str, Any]) -> Any:
        client = self._get_sonarr()
        series_id = arguments.get("series_id")
        if series_id is None:
            return await client.get_all_series()
        return await client.get_series(int(series_id))

    async def _tool_sonarr_get_calendar(self, arguments: dict[str, Any]) -> Any:
        return await self._get_sonarr().get_calendar(
            start=arguments.get("start"),
            end=arguments.get("end"),
        )

    async def _tool_sonarr_get_queue(self, arguments: dict[str, Any]) -> Any:
        return await self._get_sonarr().get_queue(
            page=int(arguments.get("page", 1)),
            page_size=int(arguments.get("page_size", 20)),
            include_unknown_series=bool(arguments.get("include_unknown_series", False)),
        )

    async def _tool_sonarr_get_history(self, arguments: dict[str, Any]) -> Any:
        return await self._get_sonarr().get_history(
            page=int(arguments.get("page", 1)),
            page_size=int(arguments.get("page_size", 20)),
            series_id=arguments.get("series_id"),
            event_type=arguments.get("event_type"),
        )

    async def _tool_sonarr_get_root_folders(self, _: dict[str, Any]) -> Any:
        return await self._get_sonarr().get_root_folders()

    async def _tool_sonarr_get_quality_profiles(self, _: dict[str, Any]) -> Any:
        return await self._get_sonarr().get_quality_profiles()

    async def _tool_sonarr_get_episodes(self, arguments: dict[str, Any]) -> Any:
        return await self._get_sonarr().get_episodes(int(self._require(arguments, "series_id")))

    async def _tool_sonarr_get_episode(self, arguments: dict[str, Any]) -> Any:
        return await self._get_sonarr().get_episode(int(self._require(arguments, "episode_id")))

    async def _tool_sonarr_update_series(self, arguments: dict[str, Any]) -> Any:
        client = self._get_sonarr()
        series_id = int(self._require(arguments, "series_id"))
        fields = self._require_object(arguments, "fields")
        series = await client.get_series(series_id)
        series.update(fields)
        return await client.update_series(series)

    # --- new sonarr handlers -------------------------------------------------

    async def _tool_sonarr_delete_queue_item(self, arguments: dict[str, Any]) -> Any:
        client = self._get_sonarr()
        queue_id = int(self._require(arguments, "queue_id"))
        await client.delete_queue_item(
            queue_id=queue_id,
            remove_from_client=bool(arguments.get("remove_from_client", True)),
            blocklist=bool(arguments.get("blocklist", False)),
        )
        return {
            "success": True,
            "queue_id": queue_id,
            "remove_from_client": bool(arguments.get("remove_from_client", True)),
            "blocklist": bool(arguments.get("blocklist", False)),
        }

    async def _tool_sonarr_search_episode(self, arguments: dict[str, Any]) -> Any:
        episode_id = int(self._require(arguments, "episode_id"))
        return await self._get_sonarr().search_episode(episode_id)

    async def _tool_sonarr_trigger_command(self, arguments: dict[str, Any]) -> Any:
        name = self._require_string(arguments, "name")
        payload = arguments.get("payload") or {}
        if not isinstance(payload, dict):
            raise ToolInvocationError(
                "payload must be an object", service="sonarr", operation="trigger_command"
            )
        return await self._get_sonarr().trigger_command(name, **payload)

    async def _tool_sonarr_interactive_search(self, arguments: dict[str, Any]) -> Any:
        episode_id = arguments.get("episode_id")
        series_id = arguments.get("series_id")
        season_number = arguments.get("season_number")
        if episode_id is None and series_id is None:
            raise ToolInvocationError(
                "Provide episode_id or series_id (with optional season_number)",
                service="sonarr",
                operation="interactive_search",
            )
        return await self._get_sonarr().interactive_search(
            episode_id=int(episode_id) if episode_id is not None else None,
            series_id=int(series_id) if series_id is not None else None,
            season_number=int(season_number) if season_number is not None else None,
        )

    async def _tool_sonarr_grab_release(self, arguments: dict[str, Any]) -> Any:
        guid = self._require_string(arguments, "guid")
        indexer_id = int(self._require(arguments, "indexer_id"))
        should_override = bool(arguments.get("should_override", False))
        episode_ids = arguments.get("episode_ids")
        if episode_ids is not None:
            episode_ids = [int(x) for x in episode_ids]
        season_number = arguments.get("season_number")
        if season_number is not None:
            season_number = int(season_number)
        series_id = arguments.get("series_id")
        if series_id is not None:
            series_id = int(series_id)
        quality = arguments.get("quality")
        if quality is not None and not isinstance(quality, dict):
            raise ToolInvocationError(
                "quality must be an object", service="sonarr", operation="grab_release"
            )
        languages = arguments.get("languages")
        if languages is not None and not isinstance(languages, list):
            raise ToolInvocationError(
                "languages must be an array", service="sonarr", operation="grab_release"
            )
        return await self._get_sonarr().grab_release(
            guid=guid,
            indexer_id=indexer_id,
            should_override=should_override,
            episode_ids=episode_ids,
            season_number=season_number,
            series_id=series_id,
            quality=quality,
            languages=languages,
        )

    async def _tool_sonarr_get_custom_formats(self, _: dict[str, Any]) -> Any:
        return await self._get_sonarr().get_custom_formats()

    async def _tool_sonarr_create_custom_format(self, arguments: dict[str, Any]) -> Any:
        payload = self._require_object(arguments, "payload")
        return await self._get_sonarr().create_custom_format(payload)

    async def _tool_sonarr_update_custom_format(self, arguments: dict[str, Any]) -> Any:
        custom_format_id = int(self._require(arguments, "custom_format_id"))
        payload = self._require_object(arguments, "payload")
        return await self._get_sonarr().update_custom_format(custom_format_id, payload)

    async def _tool_sonarr_update_quality_profile(self, arguments: dict[str, Any]) -> Any:
        profile_id = int(self._require(arguments, "profile_id"))
        payload = self._require_object(arguments, "payload")
        return await self._get_sonarr().update_quality_profile(profile_id, payload)

    async def _tool_sonarr_list_indexers(self, _: dict[str, Any]) -> Any:
        return await self._get_sonarr().get_all_indexers()

    async def _tool_sonarr_delete_indexer(self, arguments: dict[str, Any]) -> Any:
        indexer_id = int(self._require(arguments, "indexer_id"))
        await self._get_sonarr().delete_indexer(indexer_id)
        return {"deleted": True, "indexer_id": indexer_id}

    async def _tool_sonarr_get_blocklist(self, arguments: dict[str, Any]) -> Any:
        return await self._get_sonarr().get_blocklist(
            page=int(arguments.get("page", 1)),
            page_size=int(arguments.get("page_size", 20)),
            sort_key=arguments.get("sort_key", "date"),
            sort_direction=arguments.get("sort_direction", "descending"),
        )

    async def _tool_sonarr_delete_blocklist_item(self, arguments: dict[str, Any]) -> Any:
        blocklist_id = int(self._require(arguments, "blocklist_id"))
        await self._get_sonarr().delete_blocklist_item(blocklist_id)
        return {"deleted": True, "blocklist_id": blocklist_id}

    async def _tool_sonarr_delete_blocklist_bulk(self, arguments: dict[str, Any]) -> Any:
        ids = arguments.get("blocklist_ids")
        if not isinstance(ids, list):
            raise ToolInvocationError(
                "blocklist_ids must be an array",
                service="sonarr",
                operation="delete_blocklist_bulk",
            )
        ids = [int(x) for x in ids]
        await self._get_sonarr().delete_blocklist_bulk(ids)
        return {"deleted": True, "count": len(ids)}

    async def _tool_sonarr_list_release_profiles(self, _: dict[str, Any]) -> Any:
        return await self._get_sonarr().get_release_profiles()

    async def _tool_sonarr_create_release_profile(self, arguments: dict[str, Any]) -> Any:
        payload = self._require_object(arguments, "payload")
        return await self._get_sonarr().create_release_profile(payload)

    async def _tool_sonarr_update_release_profile(self, arguments: dict[str, Any]) -> Any:
        profile_id = int(self._require(arguments, "profile_id"))
        payload = self._require_object(arguments, "payload")
        return await self._get_sonarr().update_release_profile(profile_id, payload)

    async def _tool_sonarr_delete_release_profile(self, arguments: dict[str, Any]) -> Any:
        profile_id = int(self._require(arguments, "profile_id"))
        await self._get_sonarr().delete_release_profile(profile_id)
        return {"deleted": True, "profile_id": profile_id}

    async def _tool_sonarr_get_manual_import_candidates(self, arguments: dict[str, Any]) -> Any:
        return await self._get_sonarr().get_manual_import_candidates(
            folder=arguments.get("folder"),
            download_id=arguments.get("download_id"),
            series_id=int(arguments["series_id"]) if arguments.get("series_id") is not None else None,
            filter_existing_files=bool(arguments.get("filter_existing_files", True)),
        )

    async def _tool_sonarr_execute_manual_import(self, arguments: dict[str, Any]) -> Any:
        files = arguments.get("files")
        if not isinstance(files, list):
            raise ToolInvocationError(
                "files must be an array",
                service="sonarr",
                operation="execute_manual_import",
            )
        import_mode = arguments.get("import_mode", "auto")
        return await self._get_sonarr().execute_manual_import(files=files, import_mode=import_mode)

    async def _tool_radarr_search_movie(self, arguments: dict[str, Any]) -> Any:
        return await self._get_radarr().lookup_movie(self._require_string(arguments, "term"))

    async def _tool_radarr_add_movie(self, arguments: dict[str, Any]) -> Any:
        return await self._get_radarr().add_movie(**arguments)

    async def _tool_radarr_get_movies(self, arguments: dict[str, Any]) -> Any:
        client = self._get_radarr()
        movie_id = arguments.get("movie_id")
        if movie_id is None:
            return await client.get_all_movies()
        return await client.get_movie(int(movie_id))

    async def _tool_radarr_get_calendar(self, arguments: dict[str, Any]) -> Any:
        return await self._get_radarr().get_calendar(
            start=arguments.get("start"),
            end=arguments.get("end"),
        )

    async def _tool_radarr_get_queue(self, arguments: dict[str, Any]) -> Any:
        return await self._get_radarr().get_queue(
            page=int(arguments.get("page", 1)),
            page_size=int(arguments.get("page_size", 20)),
            include_unknown_movies=bool(arguments.get("include_unknown_movies", False)),
        )

    async def _tool_radarr_get_history(self, arguments: dict[str, Any]) -> Any:
        return await self._get_radarr().get_history(
            page=int(arguments.get("page", 1)),
            page_size=int(arguments.get("page_size", 20)),
            movie_id=arguments.get("movie_id"),
            event_type=arguments.get("event_type"),
        )

    async def _tool_radarr_get_root_folders(self, _: dict[str, Any]) -> Any:
        return await self._get_radarr().get_root_folders()

    async def _tool_radarr_get_quality_profiles(self, _: dict[str, Any]) -> Any:
        return await self._get_radarr().get_quality_profiles()

    async def _tool_radarr_get_movie(self, arguments: dict[str, Any]) -> Any:
        return await self._get_radarr().get_movie(int(self._require(arguments, "movie_id")))

    async def _tool_radarr_lookup_movie(self, arguments: dict[str, Any]) -> Any:
        return await self._get_radarr().lookup_movie(self._require_string(arguments, "term"))

    async def _tool_radarr_update_movie(self, arguments: dict[str, Any]) -> Any:
        client = self._get_radarr()
        movie_id = int(self._require(arguments, "movie_id"))
        fields = self._require_object(arguments, "fields")
        movie = await client.get_movie(movie_id)
        movie.update(fields)
        return await client.update_movie(movie)

    async def _tool_radarr_list_indexers(self, _: dict[str, Any]) -> Any:
        return await self._get_radarr().get_all_indexers()

    async def _tool_radarr_delete_indexer(self, arguments: dict[str, Any]) -> Any:
        indexer_id = int(self._require(arguments, "indexer_id"))
        await self._get_radarr().delete_indexer(indexer_id)
        return {"deleted": True, "indexer_id": indexer_id}

    async def _tool_radarr_get_blocklist(self, arguments: dict[str, Any]) -> Any:
        return await self._get_radarr().get_blocklist(
            page=int(arguments.get("page", 1)),
            page_size=int(arguments.get("page_size", 20)),
            sort_key=arguments.get("sort_key", "date"),
            sort_direction=arguments.get("sort_direction", "descending"),
        )

    async def _tool_radarr_delete_blocklist_item(self, arguments: dict[str, Any]) -> Any:
        blocklist_id = int(self._require(arguments, "blocklist_id"))
        await self._get_radarr().delete_blocklist_item(blocklist_id)
        return {"deleted": True, "blocklist_id": blocklist_id}

    async def _tool_radarr_delete_blocklist_bulk(self, arguments: dict[str, Any]) -> Any:
        ids = arguments.get("blocklist_ids")
        if not isinstance(ids, list):
            raise ToolInvocationError(
                "blocklist_ids must be an array",
                service="radarr",
                operation="delete_blocklist_bulk",
            )
        ids = [int(x) for x in ids]
        await self._get_radarr().delete_blocklist_bulk(ids)
        return {"deleted": True, "count": len(ids)}

    async def _tool_prowlarr_search(self, arguments: dict[str, Any]) -> Any:
        return await self._get_prowlarr().search(
            query=self._require_string(arguments, "query"),
            type=arguments.get("type", "search"),
        )

    async def _tool_prowlarr_get_indexers(self, arguments: dict[str, Any]) -> Any:
        result = await self._get_prowlarr().get_all_indexers()
        if not bool(arguments.get("include_secrets", False)):
            result = _redact_indexer_secrets(result)
        return result

    async def _tool_prowlarr_get_indexer_schema(self, arguments: dict[str, Any]) -> Any:
        result = await self._get_prowlarr().get_indexer_schema(
            definition_name=arguments.get("definition_name"),
        )
        if not bool(arguments.get("include_secrets", False)):
            result = _redact_indexer_secrets(result)
        return result

    async def _tool_prowlarr_add_indexer(self, arguments: dict[str, Any]) -> Any:
        payload = self._require_object(arguments, "payload")
        return await self._get_prowlarr().add_indexer(payload)

    async def _tool_prowlarr_update_indexer(self, arguments: dict[str, Any]) -> Any:
        indexer_id = int(self._require(arguments, "indexer_id"))
        payload = self._require_object(arguments, "payload")
        payload = {**payload, "id": indexer_id}
        return await self._get_prowlarr().update_indexer(payload)

    async def _tool_prowlarr_delete_indexer(self, arguments: dict[str, Any]) -> Any:
        indexer_id = int(self._require(arguments, "indexer_id"))
        await self._get_prowlarr().delete_indexer(indexer_id)
        return {"deleted": True, "indexer_id": indexer_id}

    async def _tool_prowlarr_sync_apps(self, _: dict[str, Any]) -> Any:
        return await self._get_prowlarr().sync_all_applications()

    async def _tool_prowlarr_get_applications(self, _: dict[str, Any]) -> Any:
        return await self._get_prowlarr().get_applications()

    async def _tool_prowlarr_get_download_clients(self, _: dict[str, Any]) -> Any:
        return await self._get_prowlarr().get_download_clients()

    async def _tool_prowlarr_test_indexer(self, arguments: dict[str, Any]) -> Any:
        client = self._get_prowlarr()
        indexer_id = int(self._require(arguments, "indexer_id"))
        indexer = await client.get_indexer(indexer_id)
        return await client.test_indexer(indexer)

    async def _tool_prowlarr_test_all_indexers(self, _: dict[str, Any]) -> Any:
        return await self._get_prowlarr().test_all_indexers()

    async def _tool_prowlarr_get_tags(self, _: dict[str, Any]) -> Any:
        return await self._get_prowlarr().get_tags()

    async def _tool_prowlarr_get_system_health(self, _: dict[str, Any]) -> Any:
        return await self._get_prowlarr().get_system_health()

    def _route_intent_to_tool(
        self,
        service: ArrService,
        operation: OperationType,
    ) -> Optional[str]:
        """Route a natural-language intent to a curated tool name."""
        route_map = {
            (ArrService.SONARR, OperationType.SEARCH): "sonarr_search_series",
            (ArrService.SONARR, OperationType.LIST): "sonarr_get_series",
            (ArrService.RADARR, OperationType.SEARCH): "radarr_search_movie",
            (ArrService.RADARR, OperationType.LIST): "radarr_get_movies",
            (ArrService.PROWLARR, OperationType.SEARCH): "prowlarr_search",
            (ArrService.PROWLARR, OperationType.LIST): "prowlarr_get_indexers",
            (ArrService.PROWLARR, OperationType.SYNC): "prowlarr_sync_apps",
        }
        return route_map.get((service, operation))

    def _resolve_curated_intent(
        self,
        query: str,
    ) -> tuple[ArrService, OperationType, dict[str, Any]]:
        """Resolve intent with a small curated override layer for exposed services."""
        service, operation, context = self.router.route(query)
        query_lower = query.lower()

        if any(keyword in query_lower for keyword in ("indexer", "indexers", "tracker", "usenet")):
            return ArrService.PROWLARR, operation, context

        if any(
            keyword in query_lower
            for keyword in ("tv", "tv show", "tv shows", "series", "episode", "season", "anime")
        ):
            return ArrService.SONARR, operation, context

        if any(
            keyword in query_lower for keyword in ("movie", "movies", "film", "films", "cinema", "tmdb")
        ):
            return ArrService.RADARR, operation, context

        if service.value in self.clients:
            return service, operation, context

        if operation == OperationType.SEARCH:
            return ArrService.RADARR, operation, context

        return service, operation, context

    def _arguments_for_routed_tool(
        self,
        tool_name: str,
        query: str,
        context: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Build tool arguments for the routed natural-language action."""
        if tool_name in {"sonarr_search_series", "radarr_search_movie"}:
            return tool_name, {"term": context.get("title") or query}
        if tool_name == "prowlarr_search":
            return tool_name, {"query": context.get("title") or query, "type": "search"}
        return tool_name, {}

    def _get_sonarr(self) -> SonarrClient:
        return self.clients["sonarr"]

    def _get_radarr(self) -> RadarrClient:
        return self.clients["radarr"]

    def _get_prowlarr(self) -> ProwlarrClient:
        return self.clients["prowlarr"]

    @staticmethod
    def _require(arguments: dict[str, Any], key: str) -> Any:
        """Require a key to be present in the tool arguments."""
        if key not in arguments:
            raise ValueError(f"Missing required argument: {key}")
        return arguments[key]

    @classmethod
    def _require_string(cls, arguments: dict[str, Any], key: str) -> str:
        """Require a non-empty string argument."""
        value = cls._require(arguments, key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Argument '{key}' must be a non-empty string")
        return value.strip()

    @classmethod
    def _require_object(cls, arguments: dict[str, Any], key: str) -> dict[str, Any]:
        """Require an object argument."""
        value = cls._require(arguments, key)
        if not isinstance(value, dict):
            raise ValueError(f"Argument '{key}' must be an object")
        return value

    @staticmethod
    def _error_payload(
        *,
        service: Optional[str],
        operation: Optional[str],
        message: str,
        http_status: Optional[int] = None,
        details: Optional[dict[str, Any]] = None,
        tool: Optional[str] = None,
    ) -> dict[str, Any]:
        """Build the normalized error response shape."""
        return {
            "ok": False,
            "tool": tool,
            "service": service,
            "operation": operation,
            "http_status": http_status,
            "message": message,
            "details": details or {},
        }

    @staticmethod
    def _serialize_payload(payload: dict[str, Any]) -> str:
        """Serialize a payload into JSON text for the MCP response."""
        return json.dumps(payload, indent=2, default=str)

    async def run(self) -> None:
        """Run the MCP server over stdio."""
        from mcp.server.stdio import stdio_server

        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options(),
            )


def main() -> None:
    """Main entry point."""
    import sys

    server = ArrSuiteMCPServer(ArrSuiteConfig())
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
        sys.exit(0)


if __name__ == "__main__":
    main()
