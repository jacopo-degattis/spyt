"""Exception types used across Spyt."""

from __future__ import annotations

from typing import Any


class SpytError(Exception):
    """Base class for every error raised by Spyt."""


class CookieError(SpytError):
    """The exported cookie file could not be read or contains no usable cookies."""


class HeaderError(SpytError):
    """The copied request headers could not be parsed or lack authentication."""


class YouTubeAuthError(SpytError):
    """YouTube Music rejected the supplied credentials."""


class YouTubeAPIError(SpytError):
    """The YouTube Music API returned an error."""


class YouTubePlaylistNotFoundError(YouTubeAPIError):
    """The playlist does not exist or is not visible to the authenticated account."""

    def __init__(self, playlist_id: str, message: str | None = None) -> None:
        super().__init__(
            message
            or f"YouTube Music playlist {playlist_id!r} was not found or is not accessible"
        )
        self.playlist_id = playlist_id


class AuthError(SpytError):
    """Spotify refused to hand out a web-player access token."""


class SpotifyAPIError(SpytError):
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
