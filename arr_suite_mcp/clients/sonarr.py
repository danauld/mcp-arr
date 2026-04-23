"""Sonarr API client."""

from typing import Any, Optional
from .base import BaseArrClient


class SonarrClient(BaseArrClient):
    """Client for interacting with Sonarr API."""

    @property
    def service_name(self) -> str:
        return "Sonarr"

    # Series Management
    async def get_all_series(self) -> list[dict[str, Any]]:
        """Get all series in Sonarr."""
        return await self.get("series")

    async def get_series(self, series_id: int) -> dict[str, Any]:
        """Get a specific series by ID."""
        return await self.get(f"series/{series_id}")

    async def lookup_series(self, term: str) -> list[dict[str, Any]]:
        """Search for series by name."""
        return await self.get("series/lookup", params={"term": term})

    async def add_series(
        self,
        tvdb_id: int,
        quality_profile_id: int,
        root_folder_path: str,
        monitored: bool = True,
        search_for_missing: bool = True,
        season_folder: bool = True,
        **kwargs
    ) -> dict[str, Any]:
        """
        Add a new series to Sonarr.

        Args:
            tvdb_id: TVDB ID of the series
            quality_profile_id: Quality profile to use
            root_folder_path: Root folder path for the series
            monitored: Whether to monitor the series
            search_for_missing: Whether to search for missing episodes
            season_folder: Whether to use season folders
            **kwargs: Additional series options

        Returns:
            Added series data
        """
        # First lookup the series to get full data
        lookup_results = await self.lookup_series(f"tvdb:{tvdb_id}")
        if not lookup_results:
            raise ValueError(f"Series with TVDB ID {tvdb_id} not found")

        series_data = lookup_results[0]
        series_data.update({
            "qualityProfileId": quality_profile_id,
            "rootFolderPath": root_folder_path,
            "monitored": monitored,
            "seasonFolder": season_folder,
            "addOptions": {
                "searchForMissingEpisodes": search_for_missing
            },
            **kwargs
        })

        return await self.post("series", json=series_data)

    async def update_series(self, series_data: dict[str, Any]) -> dict[str, Any]:
        """Update an existing series."""
        return await self.put("series", json=series_data)

    async def delete_series(
        self,
        series_id: int,
        delete_files: bool = False
    ) -> None:
        """Delete a series."""
        await self.delete(
            f"series/{series_id}",
            params={"deleteFiles": delete_files}
        )

    # Episode Management
    async def get_episodes(self, series_id: int) -> list[dict[str, Any]]:
        """Get all episodes for a series."""
        return await self.get("episode", params={"seriesId": series_id})

    async def get_episode(self, episode_id: int) -> dict[str, Any]:
        """Get a specific episode by ID."""
        return await self.get(f"episode/{episode_id}")

    async def update_episode(self, episode_data: dict[str, Any]) -> dict[str, Any]:
        """Update an episode."""
        return await self.put("episode", json=episode_data)

    async def search_episode(self, episode_id: int) -> dict[str, Any]:
        """Trigger a search for a specific episode."""
        return await self.post(
            "command",
            json={"name": "EpisodeSearch", "episodeIds": [episode_id]}
        )

    async def search_series(self, series_id: int) -> dict[str, Any]:
        """Trigger a search for all missing episodes in a series."""
        return await self.post(
            "command",
            json={"name": "SeriesSearch", "seriesId": series_id}
        )

    # Quality Profiles
    async def get_quality_profiles(self) -> list[dict[str, Any]]:
        """Get all quality profiles."""
        return await self.get("qualityprofile")

    async def get_quality_profile(self, profile_id: int) -> dict[str, Any]:
        """Get a specific quality profile."""
        return await self.get(f"qualityprofile/{profile_id}")

    # Root Folders
    async def get_root_folders(self) -> list[dict[str, Any]]:
        """Get all root folders."""
        return await self.get("rootfolder")

    # Tags
    async def get_tags(self) -> list[dict[str, Any]]:
        """Get all tags."""
        return await self.get("tag")

    async def create_tag(self, label: str) -> dict[str, Any]:
        """Create a new tag."""
        return await self.post("tag", json={"label": label})

    # Queue
    async def get_queue(
        self,
        page: int = 1,
        page_size: int = 20,
        include_unknown_series: bool = False
    ) -> dict[str, Any]:
        """Get the download queue."""
        return await self.get(
            "queue",
            params={
                "page": page,
                "pageSize": page_size,
                "includeUnknownSeriesItems": include_unknown_series
            }
        )

    async def delete_queue_item(
        self,
        queue_id: int,
        remove_from_client: bool = True,
        blocklist: bool = False
    ) -> None:
        """Remove an item from the queue."""
        await self.delete(
            f"queue/{queue_id}",
            params={
                "removeFromClient": remove_from_client,
                "blocklist": blocklist
            }
        )

    # History
    async def get_history(
        self,
        page: int = 1,
        page_size: int = 20,
        series_id: Optional[int] = None,
        event_type: Optional[str] = None
    ) -> dict[str, Any]:
        """Get history of downloads and imports."""
        params = {"page": page, "pageSize": page_size}
        if series_id:
            params["seriesId"] = series_id
        if event_type:
            params["eventType"] = event_type
        return await self.get("history", params=params)

    # Calendar
    async def get_calendar(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Get upcoming episodes."""
        params = {}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return await self.get("calendar", params=params)

    # Commands
    async def refresh_series(self, series_id: int) -> dict[str, Any]:
        """Refresh series information from TVDB."""
        return await self.post(
            "command",
            json={"name": "RefreshSeries", "seriesId": series_id}
        )

    async def rescan_series(self, series_id: int) -> dict[str, Any]:
        """Rescan series files on disk."""
        return await self.post(
            "command",
            json={"name": "RescanSeries", "seriesId": series_id}
        )

    async def rename_series(self, series_id: int) -> dict[str, Any]:
        """Rename series files."""
        return await self.post(
            "command",
            json={"name": "RenameSeries", "seriesIds": [series_id]}
        )

    async def backup_database(self) -> dict[str, Any]:
        """Trigger a database backup."""
        return await self.post("command", json={"name": "Backup"})

    # Config
    async def get_config(self, section: str) -> dict[str, Any]:
        """
        Get configuration for a specific section.

        Args:
            section: Config section (e.g., 'ui', 'naming', 'mediamanagement')
        """
        return await self.get(f"config/{section}")

    async def update_config(
        self,
        section: str,
        config_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Update configuration for a specific section."""
        return await self.put(f"config/{section}", json=config_data)

    # Interactive release search + manual grab
    # Targets: /api/v3/release (GET to list available releases, POST to grab one)
    async def interactive_search(
        self,
        episode_id: Optional[int] = None,
        series_id: Optional[int] = None,
        season_number: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """
        Return all available releases for an episode, season, or series search.

        Exactly one of episode_id / series_id (alone or with season_number) must
        be provided. Results include score, quality, rejection reasons, guid,
        and indexer id — what the UI's interactive-search grid shows.
        """
        params: dict[str, Any] = {}
        if episode_id is not None:
            params["episodeId"] = episode_id
        elif series_id is not None:
            params["seriesId"] = series_id
            if season_number is not None:
                params["seasonNumber"] = season_number
        else:
            raise ValueError("interactive_search requires episode_id or series_id")
        return await self.get("release", params=params)

    async def grab_release(
        self,
        guid: str,
        indexer_id: int,
        *,
        should_override: bool = False,
        episode_ids: Optional[list[int]] = None,
        season_number: Optional[int] = None,
        series_id: Optional[int] = None,
        quality: Optional[dict[str, Any]] = None,
        languages: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """
        Manually grab (i.e. push to the download client) a specific release.

        `guid` and `indexer_id` come from a prior interactive_search row.
        This is the same action as clicking a manual-download row in the UI.

        When ``should_override`` is True, any supplied override fields are sent
        to Sonarr's "Override and add to Download Queue" path — this force-maps
        the release to the provided episode/season/series/quality/languages
        regardless of what Sonarr's parser inferred from the filename. Useful
        for scene-numbering mismatches (e.g. SpaZe AEW releases where `342`
        is parsed as S3E42).
        """
        payload: dict[str, Any] = {"guid": guid, "indexerId": indexer_id}
        if should_override:
            payload["shouldOverride"] = True
            if episode_ids is not None:
                payload["episodeIds"] = episode_ids
            if season_number is not None:
                payload["seasonNumber"] = season_number
            if series_id is not None:
                payload["seriesId"] = series_id
            if quality is not None:
                payload["quality"] = quality
            if languages is not None:
                payload["languages"] = languages
        return await self.post("release", json=payload)

    # Generic command trigger (covers EpisodeSearch, SeasonSearch, SeriesSearch,
    # RefreshSeries, Rescan, RenameSeries, RssSync, MissingEpisodeSearch, etc.)
    async def trigger_command(
        self,
        name: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Trigger a Sonarr command by name, passing through any extra kwargs as JSON body."""
        payload: dict[str, Any] = {"name": name}
        for k, v in kwargs.items():
            if v is not None:
                payload[k] = v
        return await self.post("command", json=payload)

    # Custom Formats
    async def get_custom_formats(self) -> list[dict[str, Any]]:
        """List all custom formats."""
        return await self.get("customformat")

    async def get_custom_format(self, custom_format_id: int) -> dict[str, Any]:
        """Get a single custom format by id."""
        return await self.get(f"customformat/{custom_format_id}")

    async def create_custom_format(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Create a new custom format.

        Payload shape follows Sonarr v3's /customformat POST schema:
            {
                "name": "...",
                "includeCustomFormatWhenRenaming": false,
                "specifications": [
                    {"name": "...", "implementation": "ReleaseTitleSpecification",
                     "negate": false, "required": false,
                     "fields": [{"name":"value","value":"..."}]}
                ]
            }
        """
        return await self.post("customformat", json=payload)

    async def update_custom_format(
        self,
        custom_format_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Update an existing custom format. Payload must include `id` matching the URL."""
        payload = {**payload, "id": custom_format_id}
        return await self.put(f"customformat/{custom_format_id}", json=payload)

    async def update_quality_profile(
        self,
        profile_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Update an existing quality profile.

        Pass the full profile object merged with edits (e.g. formatItems with
        new scores, cutoff, minFormatScore). Use get_quality_profile first to
        retrieve the current shape, then modify and send back.
        """
        payload = {**payload, "id": profile_id}
        return await self.put(f"qualityprofile/{profile_id}", json=payload)

    # Indexers
    async def get_all_indexers(self) -> list[dict[str, Any]]:
        """List all indexers configured in Sonarr (includes Prowlarr-synced entries suffixed '(Prowlarr)')."""
        return await self.get("indexer")

    async def delete_indexer(self, indexer_id: int) -> Any:
        """Delete an indexer. Does not affect Prowlarr; Prowlarr-synced indexers reappear on next sync."""
        return await self.delete(f"indexer/{indexer_id}")

    # Blocklist
    async def get_blocklist(
        self,
        page: int = 1,
        page_size: int = 20,
        sort_key: str = "date",
        sort_direction: str = "descending",
    ) -> dict[str, Any]:
        """Paginated list of blocklisted releases."""
        return await self.get(
            "blocklist",
            params={
                "page": page,
                "pageSize": page_size,
                "sortKey": sort_key,
                "sortDirection": sort_direction,
            },
        )

    async def delete_blocklist_item(self, blocklist_id: int) -> Any:
        """Remove a single entry from the blocklist."""
        return await self.delete(f"blocklist/{blocklist_id}")

    async def delete_blocklist_bulk(self, blocklist_ids: list[int]) -> Any:
        """Remove many blocklist entries in one call."""
        return await self.delete("blocklist/bulk", json={"ids": blocklist_ids})

    # Release Profiles
    async def get_release_profiles(self) -> list[dict[str, Any]]:
        return await self.get("releaseprofile")

    async def create_release_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.post("releaseprofile", json=payload)

    async def update_release_profile(
        self,
        profile_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {**payload, "id": profile_id}
        return await self.put(f"releaseprofile/{profile_id}", json=payload)

    async def delete_release_profile(self, profile_id: int) -> Any:
        return await self.delete(f"releaseprofile/{profile_id}")

    # Manual import
    async def get_manual_import_candidates(
        self,
        folder: Optional[str] = None,
        download_id: Optional[str] = None,
        series_id: Optional[int] = None,
        filter_existing_files: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Get import candidates for a folder or download. Returns files with
        Sonarr's best-guess episode mapping, quality, language, and rejection
        reasons — review before triggering an actual import.
        """
        params: dict[str, Any] = {
            "filterExistingFiles": "true" if filter_existing_files else "false",
        }
        if folder is not None:
            params["folder"] = folder
        if download_id is not None:
            params["downloadId"] = download_id
        if series_id is not None:
            params["seriesId"] = series_id
        return await self.get("manualimport", params=params)

    async def execute_manual_import(
        self,
        files: list[dict[str, Any]],
        import_mode: str = "auto",
    ) -> dict[str, Any]:
        """
        Import specific files (typically from a prior get_manual_import_candidates).

        Each file entry must include path, seriesId, episodeIds, quality, languages.
        import_mode is one of 'auto', 'move', 'copy'.
        """
        return await self.post(
            "command",
            json={"name": "ManualImport", "files": files, "importMode": import_mode},
        )
