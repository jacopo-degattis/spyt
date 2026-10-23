"""Map ``ytmusicapi`` playlist dictionaries onto Spyt's stable models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .models import Album, Artist, Owner, Playlist, SearchTrack, SkippedItem, Track


def map_playlist(
    raw: dict[str, Any],
    *,
    playlist_id: str,
    include_episodes: bool = False,
    max_items: int | None = None,
    fetched_at: str | None = None,
) -> Playlist:
    tracks: list[Track] = []
    skipped: list[SkippedItem] = []

    raw_tracks = raw.get("tracks") or []
    if not isinstance(raw_tracks, list):
        raw_tracks = []

    for index, item in enumerate(raw_tracks):
        if max_items is not None and index >= max_items:
            break
        position = index + 1
        if not isinstance(item, dict):
            skipped.append(SkippedItem(position=position, reason="unknown"))
            continue

        mapped = _map_track(item, position)
        if mapped is None:
            skipped.append(
                SkippedItem(
                    position=position,
                    reason="deleted",
                    video_id=_string_or_none(item.get("videoId")),
                    title=_string_or_none(item.get("title")),
                )
            )
            continue

        if mapped.kind == "episode" and not include_episodes:
            skipped.append(
                SkippedItem(
                    position=position,
                    reason="episode",
                    video_id=mapped.video_id,
                    title=mapped.title,
                )
            )
            continue

        tracks.append(mapped)

    return Playlist(
        id=playlist_id,
        url=f"https://music.youtube.com/playlist?list={playlist_id}",
        title=raw.get("title") or "",
        description=raw.get("description") or "",
        owner=_map_owner(raw),
        year=_string_or_none(raw.get("year")),
        privacy=_string_or_none(raw.get("privacy")),
        cover_url=_best_thumbnail(raw.get("thumbnails")),
        reported_track_count=_as_int(raw.get("trackCount")),
        duration_text=_string_or_none(raw.get("duration")),
        duration_seconds=_as_int(raw.get("duration_seconds")),
        fetched_at=fetched_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        tracks=tracks,
        skipped=skipped,
    )


def map_search_result(item: dict[str, Any]) -> SearchTrack | None:
    """Map one result from ``YTMusic.search`` onto :class:`SearchTrack`."""
    video_id = item.get("videoId")
    title = item.get("title")
    if not isinstance(video_id, str) or not video_id:
        return None
    if not isinstance(title, str):
        title = ""

    artists = [
        Artist(name=artist["name"], id=_string_or_none(artist.get("id")))
        for artist in item.get("artists") or []
        if isinstance(artist, dict) and isinstance(artist.get("name"), str) and artist["name"]
    ]

    album = None
    album_data = item.get("album")
    if isinstance(album_data, dict) and isinstance(album_data.get("name"), str):
        album = Album(name=album_data["name"], id=_string_or_none(album_data.get("id")))

    video_type = _string_or_none(item.get("videoType"))
    kind = _kind_from_video_type(video_type)
    if kind == "unknown":
        result_type = item.get("resultType")
        if result_type == "song":
            kind = "song"
        elif result_type == "video":
            kind = "video"
        elif result_type == "episode":
            kind = "episode"

    available = item.get("isAvailable")

    return SearchTrack(
        video_id=video_id,
        title=title,
        kind=kind,
        artists=artists,
        album=album,
        duration_seconds=_as_int(item.get("duration_seconds")),
        explicit=bool(item.get("isExplicit")),
        available=available if isinstance(available, bool) else True,
    )


def _map_track(item: dict[str, Any], position: int) -> Track | None:
    title = item.get("title")
    video_id = item.get("videoId")

    if not isinstance(title, str) and not isinstance(video_id, str):
        return None
    if title == "Song deleted":
        return None

    artists: list[Artist] = []
    for artist in item.get("artists") or []:
        if not isinstance(artist, dict):
            continue
        name = artist.get("name")
        if isinstance(name, str) and name:
            artists.append(Artist(name=name, id=_string_or_none(artist.get("id"))))

    album = None
    album_data = item.get("album")
    if isinstance(album_data, dict) and (
        isinstance(album_data.get("name"), str) or isinstance(album_data.get("id"), str)
    ):
        album = Album(
            name=_string_or_none(album_data.get("name")),
            id=_string_or_none(album_data.get("id")),
        )

    duration_seconds = _as_int(item.get("duration_seconds"))
    video_type = _string_or_none(item.get("videoType"))
    available = item.get("isAvailable")

    return Track(
        position=position,
        video_id=_string_or_none(video_id),
        title=title if isinstance(title, str) else "",
        kind=_kind_from_video_type(video_type),
        artists=artists,
        album=album,
        duration_ms=duration_seconds * 1000 if duration_seconds is not None else None,
        duration_text=_string_or_none(item.get("duration")),
        explicit=bool(item.get("isExplicit")),
        available=available if isinstance(available, bool) else True,
        video_type=video_type,
        set_video_id=_string_or_none(item.get("setVideoId")),
        cover_url=_best_thumbnail(item.get("thumbnails")),
    )


def _map_owner(raw: dict[str, Any]) -> Owner | None:
    author = raw.get("author")
    if isinstance(author, dict):
        name = _string_or_none(author.get("name"))
        author_id = _string_or_none(author.get("id"))
        if name or author_id:
            return Owner(name=name, id=author_id)

    collaborators = raw.get("collaborators")
    if isinstance(collaborators, dict):
        text = _string_or_none(collaborators.get("text"))
        if text:
            return Owner(name=text)
    return None


def _kind_from_video_type(video_type: str | None) -> str:
    if not video_type:
        return "unknown"
    upper = video_type.upper()
    if "PODCAST" in upper or "EPISODE" in upper:
        return "episode"
    if "ATV" in upper or "PRIVATELY_OWNED_TRACK" in upper:
        return "song"
    if "OMV" in upper or "UGC" in upper or "MUSIC_VIDEO" in upper:
        return "video"
    return "unknown"


def _best_thumbnail(thumbnails: Any) -> str | None:
    if not isinstance(thumbnails, list):
        return None

    def area(thumbnail: dict[str, Any]) -> int:
        width = thumbnail.get("width")
        height = thumbnail.get("height")
        if not isinstance(width, int) or not isinstance(height, int):
            return 0
        return width * height

    valid = [
        thumbnail
        for thumbnail in thumbnails
        if isinstance(thumbnail, dict) and thumbnail.get("url")
    ]
    if not valid:
        return None
    return max(valid, key=area).get("url")


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
