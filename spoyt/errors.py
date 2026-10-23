"""Exception types used across Spoyt."""

from __future__ import annotations

from typing import Any


class SpoytError(Exception):
    """Base class for every error raised by Spoyt."""


class CookieError(SpoytError):
    """The exported cookie file could not be read or contains no usable cookies."""


class AuthError(SpoytError):
    """Spotify refused to hand out a web-player access token."""


class SpotifyAPIError(SpoytError):
    """The Spotify pathfinder API returned an error."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class PlaylistNotFoundError(SpotifyAPIError):
    """The playlist does not exist or is not visible to the authenticated account."""

    def __init__(self, playlist_id: str, message: str | None = None) -> None:
        super().__init__(message or f"Playlist {playlist_id!r} was not found or is not accessible")
        self.playlist_id = playlist_id


class PersistedQueryNotFoundError(SpotifyAPIError):
    """Spotify retired the persisted-query hash; the client can try to rediscover it."""
