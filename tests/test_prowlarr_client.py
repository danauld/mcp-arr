"""Tests for Prowlarr compatibility behaviors."""

from __future__ import annotations

from typing import Any

import pytest

from arr_suite_mcp.clients.prowlarr import ProwlarrClient


@pytest.mark.asyncio
async def test_sync_all_applications_uses_application_indexer_sync() -> None:
    """The all-app sync command must use the Prowlarr v1 command name."""
    client = ProwlarrClient(base_url="http://example", api_key="token")
    calls: list[tuple[str, dict[str, Any] | None]] = []

    async def fake_post(endpoint: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append((endpoint, json))
        return {"ok": True}

    client.post = fake_post  # type: ignore[method-assign]
    try:
        result = await client.sync_all_applications()
        assert result == {"ok": True}
        assert calls == [("command", {"name": "ApplicationIndexerSync"})]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_sync_application_uses_application_indexer_sync() -> None:
    """The single-app sync command must use the same v1-compatible payload."""
    client = ProwlarrClient(base_url="http://example", api_key="token")
    calls: list[tuple[str, dict[str, Any] | None]] = []

    async def fake_post(endpoint: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append((endpoint, json))
        return {"ok": True}

    client.post = fake_post  # type: ignore[method-assign]
    try:
        result = await client.sync_application(7)
        assert result == {"ok": True}
        assert calls == [("command", {"name": "ApplicationIndexerSync", "applicationId": 7})]
    finally:
        await client.close()
