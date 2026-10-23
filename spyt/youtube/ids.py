"""Parsing of YouTube Music playlist references."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from ..errors import SpytError

_PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,200}$")
_BROWSE_PATH_RE = re.compile(r"/browse/(VL[A-Za-z0-9_-]+)")


class InvalidPlaylistReference(SpytError):
    """The supplied value does not look like a YouTube Music playlist reference."""


def parse_playlist_id(value: str) -> str:
    """Accept a playlist URL, a ``VL...`` browse id or a bare playlist id.

    Examples
    --------
    >>> parse_playlist_id("https://music.youtube.com/playlist?list=PLabc123")
    'PLabc123'
    >>> parse_playlist_id("VLPLabc123")
    'PLabc123'
    """
    if not isinstance(value, str):
        raise InvalidPlaylistReference("Playlist reference must be a string")

    value = value.strip()
    if not value:
        raise InvalidPlaylistReference("Playlist reference is empty")

    playlist_id = value

    if "://" in value or value.startswith("www.") or "/" in value:
        parsed = urlparse(value if "://" in value else f"https://{value}")
        query = parse_qs(parsed.query)
        list_values = query.get("list")
        if list_values and list_values[0].strip():
            playlist_id = list_values[0].strip()
        else:
            match = _BROWSE_PATH_RE.search(parsed.path)
            if not match:
                raise InvalidPlaylistReference(
                    f"{value!r} does not contain a YouTube Music playlist id"
                )
            playlist_id = match.group(1)

    playlist_id = playlist_id.removeprefix("VL")

    if not _PLAYLIST_ID_RE.match(playlist_id):
        raise InvalidPlaylistReference(f"{value!r} is not a valid YouTube Music playlist id")
    return playlist_id
