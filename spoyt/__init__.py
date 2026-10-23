"""Spoyt - move your Spotify playlists to YouTube Music."""

from .errors import (
    AuthError,
    CookieError,
    PlaylistNotFoundError,
    SpotifyAPIError,
    SpoytError,
)
from .spotify import Playlist, SpotifyWebClient, Track, load_cookies

__all__ = [
    "AuthError",
    "CookieError",
    "Playlist",
    "PlaylistNotFoundError",
    "SpotifyAPIError",
    "SpotifyWebClient",
    "SpoytError",
    "Track",
    "load_cookies",
]

__version__ = "0.1.0"
