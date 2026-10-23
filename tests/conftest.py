from __future__ import annotations

from typing import Any

import pytest


def _track(
    *,
    track_id: str,
    name: str,
    artist: str = "Artist",
    duration_ms: int = 180000,
    explicit: bool = False,
    album: str = "Album",
) -> dict[str, Any]:
    return {
        "addedAt": {"isoString": "2024-01-02T03:04:05.000Z"},
        "addedBy": {"data": {"name": "Tester", "username": "tester"}},
        "uid": f"uid-{track_id}",
        "itemV2": {
            "__typename": "TrackResponseWrapper",
            "data": {
                "__typename": "Track",
                "uri": f"spotify:track:{track_id}",
                "name": name,
                "albumOfTrack": {
                    "uri": "spotify:album:album123",
                    "name": album,
                    "date": {"isoString": "2020-05-01T00:00:00Z"},
                    "coverArt": {
                        "sources": [
                            {"url": "https://img/small.jpg", "width": 64, "height": 64},
                            {"url": "https://img/large.jpg", "width": 640, "height": 640},
                        ]
                    },
                },
                "artists": {
                    "items": [
                        {
                            "uri": "spotify:artist:artist123",
                            "profile": {"name": artist},
                        }
                    ]
                },
                "trackDuration": {"totalMilliseconds": duration_ms},
                "contentRating": {"label": "EXPLICIT" if explicit else "NONE"},
                "playability": {"playable": True, "reason": "PLAYABLE"},
                "playcount": "42",
            },
        },
    }


def _episode(*, episode_id: str, name: str = "Episode 1") -> dict[str, Any]:
    return {
        "addedAt": {"isoString": "2024-02-02T00:00:00.000Z"},
        "itemV2": {
            "__typename": "EpisodeResponseWrapper",
            "data": {
                "__typename": "Episode",
                "uri": f"spotify:episode:{episode_id}",
                "name": name,
                "duration": {"totalMilliseconds": 60000},
                "podcastV2": {
                    "data": {
                        "uri": "spotify:show:show123",
                        "name": "A Podcast",
                        "coverArt": {
                            "sources": [
                                {"url": "https://img/show.jpg", "width": 300, "height": 300}
                            ]
                        },
                    }
                },
            },
        },
    }


def make_payload(
    items: list[dict[str, Any]],
    *,
    total: int | None = None,
    name: str = "Test Playlist",
    typename: str = "Playlist",
    message: str | None = None,
) -> dict[str, Any]:
    playlist: dict[str, Any] = {
        "__typename": typename,
        "uri": "spotify:playlist:6yNRwWCVVNLEsif1esJ1Cc",
        "name": name,
        "description": "A test playlist",
        "revisionId": "revision-1",
        "ownerV2": {
            "data": {
                "name": "Tester",
                "username": "tester",
                "uri": "spotify:user:tester",
            }
        },
        "images": {
            "items": [
                {
                    "sources": [
                        {"url": "https://img/playlist.jpg", "width": 640, "height": 640}
                    ]
                }
            ]
        },
        "content": {"items": items, "totalCount": len(items) if total is None else total},
    }
    if message:
        playlist["message"] = message
    return {"data": {"playlistV2": playlist}}


@pytest.fixture
def payload_factory():
    return make_payload


@pytest.fixture
def track_factory():
    return _track


@pytest.fixture
def episode_factory():
    return _episode
