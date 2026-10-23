"""Spyt - move your Spotify playlists to YouTube Music."""

from .errors import (
    AuthError,
    CookieError,
    PlaylistNotFoundError,
    SpotifyAPIError,
    SpytError,
)
from .match import DEFAULT_MIN_SCORE, match_track
from .spotify import Playlist, SpotifyWebClient, Track, load_cookies
from .transfer import TransferReport, match_playlist

__all__ = [
    "DEFAULT_MIN_SCORE",
    "AuthError",
    "CookieError",
    "Playlist",
    "PlaylistNotFoundError",
    "SpotifyAPIError",
    "SpotifyWebClient",
    "SpytError",
    "Track",
    "TransferReport",
    "load_cookies",
    "match_playlist",
    "match_track",
]

__version__ = "0.1.0"
