"""Orchestrate a Spotify playlist transfer to YouTube Music.

The pipeline has two stages:

1. match every Spotify track to a YouTube Music search result;
2. create a private/unlisted/public playlist and add the matched video ids.

The intermediate report is JSON, so a large transfer can be resumed (matched
tracks are reused) and inspected before anything is written to YouTube.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .match import DEFAULT_MIN_SCORE, MatchResult, match_track
from .spotify.models import Playlist as SpotifyPlaylist
from .spotify.models import Track as SpotifyTrack
from .youtube.models import SearchTrack

SCHEMA_VERSION = 1
DEFAULT_PRIVACY = "private"

ProgressCallback = Callable[[int, int], None]
TrackCallback = Callable[["MatchedTrack"], None]


@dataclass
class MatchedTrack:
    position: int
    spotify: dict[str, Any]
    youtube: dict[str, Any] | None
    status: str  # "matched", "unmatched", "skipped" or "duplicate"
    reason: str | None = None
    score: float | None = None
    query: str | None = None
    duplicate_of: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "spotify": self.spotify,
            "youtube": self.youtube,
            "status": self.status,
            "reason": self.reason,
            "score": self.score,
            "query": self.query,
            "duplicate_of": self.duplicate_of,
        }


@dataclass
class TransferReport:
    spotify: dict[str, Any]
    tracks: list[MatchedTrack]
    target: dict[str, Any] | None = None
    status: str = "matched"
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @property
    def summary(self) -> dict[str, int]:
        counts = {
            "spotify_tracks": len(self.tracks),
            "matched": 0,
            "unmatched": 0,
            "skipped": 0,
            "duplicate": 0,
            "added": 0,
        }
        for track in self.tracks:
            counts[track.status] = counts.get(track.status, 0) + 1
        if self.target and isinstance(self.target.get("added"), int):
            counts["added"] = self.target["added"]
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "source": "spotify",
            "generated_at": self.generated_at,
            "status": self.status,
            "spotify": self.spotify,
            "target": self.target,
            "summary": self.summary,
            "tracks": [track.to_dict() for track in self.tracks],
        }


def match_playlist(
    playlist: SpotifyPlaylist,
    search: Callable[..., list[SearchTrack]],
    *,
    min_score: float = DEFAULT_MIN_SCORE,
    max_items: int | None = None,
    resume: dict[int, dict[str, Any]] | None = None,
    rematch: bool = False,
    progress: ProgressCallback | None = None,
    on_track: TrackCallback | None = None,
) -> list[MatchedTrack]:
    """Match every track in ``playlist`` and return the per-track results."""
    tracks = playlist.tracks
    if max_items is not None:
        tracks = tracks[:max_items]
    total = len(tracks)

    results: list[MatchedTrack] = []
    for index, track in enumerate(tracks, start=1):
        cached = _cached_entry(resume, track, index, rematch=rematch)
        if cached is not None:
            results.append(cached)
        else:
            result = match_track(track, search, min_score=min_score)
            results.append(_from_match_result(result, track))
        if progress is not None:
            progress(index, total)
        if on_track is not None:
            on_track(results[-1])
    return results


def create_target_playlist(
    client: Any,
    report: TransferReport,
    *,
    title: str,
    description: str = "",
    privacy: str = DEFAULT_PRIVACY,
    progress: ProgressCallback | None = None,
) -> int:
    """Create the YouTube Music playlist and add every matched track.

    Returns the number of tracks added and stores the playlist details in
    ``report.target``.
    """
    video_ids = [
        track.youtube["video_id"]
        for track in report.tracks
        if track.status == "matched" and track.youtube and track.youtube.get("video_id")
    ]
    if not video_ids:
        raise ValueError("there are no matched tracks to add")

    playlist_id = client.create_playlist(
        title,
        description=description,
        privacy=privacy.upper(),
    )
    report.target = {
        "platform": "youtube_music",
        "playlist_id": playlist_id,
        "url": f"https://music.youtube.com/playlist?list={playlist_id}",
        "name": title,
        "privacy": privacy.upper(),
        "added": 0,
    }

    def on_progress(done: int, total: int) -> None:
        report.target["added"] = done
        if progress is not None:
            progress(done, total)

    added = client.add_playlist_items(playlist_id, video_ids, progress=on_progress)
    report.target["added"] = added
    report.status = "created"
    return added


def mark_duplicates(tracks: list[MatchedTrack]) -> int:
    """Downgrade repeated video ids to ``duplicate`` so they are not re-added."""
    first_position: dict[str, int] = {}
    duplicates = 0
    for track in tracks:
        if mark_duplicate(track, first_position):
            duplicates += 1
    return duplicates


def mark_duplicate(track: MatchedTrack, first_position: dict[str, int]) -> bool:
    """Mark one track as a duplicate, updating ``first_position`` as needed."""
    if track.status != "matched" or not track.youtube:
        return False
    video_id = track.youtube.get("video_id")
    if not isinstance(video_id, str) or not video_id:
        return False
    if video_id in first_position:
        track.status = "duplicate"
        track.duplicate_of = first_position[video_id]
        track.reason = f"same YouTube video as position {first_position[video_id]}"
        return True
    first_position[video_id] = track.position
    return False


def build_report(
    playlist: SpotifyPlaylist,
    tracks: list[MatchedTrack],
    *,
    target: dict[str, Any] | None = None,
    status: str = "matched",
) -> TransferReport:
    owner = playlist.owner.name if playlist.owner else None
    return TransferReport(
        spotify={
            "id": playlist.id,
            "uri": playlist.uri,
            "name": playlist.name,
            "description": playlist.description,
            "owner": owner,
            "total_items": playlist.total_items,
            "url": f"https://open.spotify.com/playlist/{playlist.id}",
        },
        tracks=tracks,
        target=target,
        status=status,
    )


def load_report(path: str | Path) -> dict[str, Any] | None:
    """Read a previous report, returning ``None`` when it cannot be reused."""
    report_path = Path(path)
    if not report_path.is_file():
        return None
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("tracks"), list):
        return None
    return data


def resume_index(report: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    """Index reusable results from a previous report by playlist position."""
    if not report:
        return {}
    index: dict[int, dict[str, Any]] = {}
    for entry in report.get("tracks") or []:
        if not isinstance(entry, dict):
            continue
        position = entry.get("position")
        if isinstance(position, int):
            index[position] = entry
    return index


def write_report(report: TransferReport, path: str | Path, *, compact: bool = False) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        report.to_dict(),
        ensure_ascii=False,
        indent=None if compact else 2,
    )
    output.write_text(text + "\n", encoding="utf-8")
    return output


def spotify_track_document(track: SpotifyTrack) -> dict[str, Any]:
    return {
        "id": track.id,
        "uri": track.uri,
        "name": track.name,
        "kind": track.kind,
        "artists": [artist.name for artist in track.artists],
        "album": track.album.name if track.album else None,
        "duration_ms": track.duration_ms,
        "explicit": track.explicit,
    }


def _from_match_result(result: MatchResult, track: SpotifyTrack) -> MatchedTrack:
    youtube = None
    if result.youtube is not None:
        youtube = {
            "video_id": result.youtube.video_id,
            "title": result.youtube.title,
            "kind": result.youtube.kind,
            "artists": [artist.name for artist in result.youtube.artists],
            "album": result.youtube.album.name if result.youtube.album else None,
            "duration_seconds": result.youtube.duration_seconds,
            "explicit": result.youtube.explicit,
        }
    score = round(result.score, 4) if result.youtube is not None else None
    return MatchedTrack(
        position=track.position,
        spotify=spotify_track_document(track),
        youtube=youtube,
        status=result.status,
        reason=result.reason,
        score=score,
        query=result.query,
    )


def _cached_entry(
    resume: dict[int, dict[str, Any]] | None,
    track: SpotifyTrack,
    position: int,
    *,
    rematch: bool,
) -> MatchedTrack | None:
    if rematch or not resume:
        return None
    entry = resume.get(position)
    if not isinstance(entry, dict):
        return None

    cached_spotify = entry.get("spotify")
    if not isinstance(cached_spotify, dict) or cached_spotify.get("id") != track.id:
        return None
    if entry.get("status") not in {"matched", "skipped"}:
        # Unmatched tracks are worth retrying on the next run.
        return None

    youtube = entry.get("youtube")
    if entry.get("status") == "matched" and (
        not isinstance(youtube, dict) or not youtube.get("video_id")
    ):
        return None

    return MatchedTrack(
        position=position,
        spotify=spotify_track_document(track),
        youtube=youtube if isinstance(youtube, dict) else None,
        status=entry["status"],
        reason=entry.get("reason"),
        score=entry.get("score"),
        query=entry.get("query"),
    )
