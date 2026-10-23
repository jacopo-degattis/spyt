"""Data model for a Spotify playlist and its tracks."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Artist:
    name: str
    uri: str | None = None


@dataclass
class Album:
    name: str | None = None
    uri: str | None = None
    cover_url: str | None = None
    release_date: str | None = None


@dataclass
class Owner:
    name: str | None = None
    uri: str | None = None
    username: str | None = None


@dataclass
class Track:
    """One playable item, normally a track but potentially an episode or local file."""

    position: int
    uri: str | None
    id: str | None
    name: str
    kind: str  # "track", "episode" or "unknown"
    artists: list[Artist] = field(default_factory=list)
    album: Album | None = None
    duration_ms: int | None = None
    explicit: bool = False
    playable: bool = True
    local: bool = False
    added_at: str | None = None
    added_by: str | None = None
    playcount: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SkippedItem:
    """A playlist position that could not be represented as a track."""

    position: int
    reason: str  # "episode", "unavailable" or "unknown"
    uri: str | None = None
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Playlist:
    id: str
    uri: str
    name: str
    description: str = ""
    owner: Owner | None = None
    cover_url: str | None = None
    total_items: int = 0
    revision_id: str | None = None
    fetched_at: str | None = None
    tracks: list[Track] = field(default_factory=list)
    skipped: list[SkippedItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["track_count"] = len(self.tracks)
        return data
