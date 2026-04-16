"""API clients for the curated Arr service set."""

from .base import (
    BaseArrClient,
    ArrClientError,
    ArrClientConnectionError,
    ArrClientAuthError,
    ArrClientNotFoundError
)
from .sonarr import SonarrClient
from .radarr import RadarrClient
from .prowlarr import ProwlarrClient

__all__ = [
    "BaseArrClient",
    "ArrClientError",
    "ArrClientConnectionError",
    "ArrClientAuthError",
    "ArrClientNotFoundError",
    "SonarrClient",
    "RadarrClient",
    "ProwlarrClient",
]
