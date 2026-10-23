"""YouTube Music support."""

from .auth import (
    BrowserSession,
    build_authorization,
    fetch_browser_session,
    parse_browser_session,
)
from .client import YouTubeMusicClient
from .headers import load_headers, parse_headers
from .ids import parse_playlist_id
from .models import Album, Artist, Owner, Playlist, SearchTrack, SkippedItem, Track

__all__ = [
    "Album",
    "Artist",
    "BrowserSession",
    "Owner",
    "Playlist",
    "SearchTrack",
    "SkippedItem",
    "Track",
    "YouTubeMusicClient",
    "build_authorization",
    "fetch_browser_session",
    "load_headers",
    "parse_browser_session",
    "parse_headers",
    "parse_playlist_id",
]
