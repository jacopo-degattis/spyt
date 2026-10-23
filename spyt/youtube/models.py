"""Data model for a YouTube Music playlist and its tracks."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Artist:
    name: str
    id: str | None = None


@dataclass
class Album:
    name: str | None = None
    id: str | None = None


@dataclass
class Owner:
    name: str | None = None
    id: str | None = None


@dataclass
class Track:
    position: int
    video_id: str | None
    title: str
    kind: str = "unknown"  # "song", "video", "episode" or "unknown"
    artists: list[Artist] = field(default_factory=list)
    album: Album | None = None
    duration_ms: int | None = None
    duration_text: str | None = None
    explicit: bool = False
    available: bool = True
    video_type: str | None = None
    set_video_id: str | None = None
    cover_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SkippedItem:
    position: int
    reason: str  # "episode", "deleted" or "unknown"
    video_id: str | None = None
    title: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchTrack:
    """One song/video returned by a YouTube Music search."""

    video_id: str
    title: str
    kind: str = "unknown"  # "song", "video", "episode" or "unknown"
    artists: list[Artist] = field(default_factory=list)
    album: Album | None = None
    duration_seconds: int | None = None
    explicit: bool = False
    available: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Playlist:
    id: str
    url: str
    title: str
    description: str = ""
    owner: Owner | None = None
    year: str | None = None
    privacy: str | None = None
    cover_url: str | None = None
    reported_track_count: int | None = None
    duration_text: str | None = None
    duration_seconds: int | None = None
    fetched_at: str | None = None
    tracks: list[Track] = field(default_factory=list)
    skipped: list[SkippedItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["track_count"] = len(self.tracks)
        return data
