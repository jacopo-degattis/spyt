"""Parsing of playlist references (URLs, URIs and plain ids)."""

from __future__ import annotations

import re

from ..errors import SpytError

_PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9]{10,64}$")
_PLAYLIST_REFERENCE_RE = re.compile(r"(?:spotify:playlist:|playlist/)([A-Za-z0-9]+)")


class InvalidPlaylistReference(SpytError):
    """The supplied value does not look like a Spotify playlist reference."""


def parse_playlist_id(value: str) -> str:
    """Accept a playlist URL, URI or bare id and return the bare id.

    Examples
    --------
    >>> parse_playlist_id("spotify:playlist:6yNRwWCVVNLEsif1esJ1Cc")
    '6yNRwWCVVNLEsif1esJ1Cc'
    >>> parse_playlist_id("https://open.spotify.com/intl-it/playlist/6yNRwWCVVNLEsif1esJ1Cc?si=x")
    '6yNRwWCVVNLEsif1esJ1Cc'
    """
    if not isinstance(value, str):
        raise InvalidPlaylistReference("Playlist reference must be a string")

    value = value.strip()
    if not value:
        raise InvalidPlaylistReference("Playlist reference is empty")

    if ":" in value or "/" in value:
        match = _PLAYLIST_REFERENCE_RE.search(value)
        if not match:
            raise InvalidPlaylistReference(
                f"{value!r} is not a Spotify playlist URL or URI"
            )
        return match.group(1)

    if not _PLAYLIST_ID_RE.match(value):
        raise InvalidPlaylistReference(f"{value!r} is not a valid Spotify playlist id")
    return value
