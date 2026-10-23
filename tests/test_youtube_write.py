from __future__ import annotations

import pytest
from ytmusicapi.exceptions import YTMusicServerError

from spyt.errors import YouTubeAPIError
from spyt.youtube.client import YouTubeMusicClient


@pytest.fixture
def client():
    return YouTubeMusicClient()


def test_search_tracks_maps_results(client):
    calls = []

    def fake_search(query, filter=None, limit=20):
        calls.append((query, filter, limit))
        return [
            {
                "videoId": "v1",
                "title": "Song",
                "resultType": "song",
                "artists": [{"name": "Artist", "id": "UC1"}],
                "album": {"name": "Album", "id": "MPRE1"},
                "duration_seconds": 180,
                "isAvailable": True,
                "isExplicit": True,
            },
            {"title": "No video id"},
            "not a dict",
        ]

    client._ytm.search = fake_search  # type: ignore[method-assign]
    results = client.search_tracks("query", filter="songs", limit=5)

    assert calls == [("query", "songs", 5)]
    assert len(results) == 1
    result = results[0]
    assert result.video_id == "v1"
    assert result.kind == "song"
    assert result.artists[0].name == "Artist"
    assert result.album.name == "Album"
    assert result.duration_seconds == 180
    assert result.explicit is True


def test_search_tracks_rejects_empty_query(client):
    with pytest.raises(ValueError):
        client.search_tracks("  ")


def test_create_playlist_returns_id_and_forwards_options(client):
    calls = []

    def fake_create(title, description, privacy_status="PRIVATE"):
        calls.append((title, description, privacy_status))
        return "PLNEW"

    client._ytm.create_playlist = fake_create  # type: ignore[method-assign]
    playlist_id = client.create_playlist(
        "Target",
        description="desc",
        privacy="unlisted",
    )

    assert playlist_id == "PLNEW"
    assert calls == [("Target", "desc", "UNLISTED")]


def test_create_playlist_rejects_error_payload(client):
    client._ytm.create_playlist = lambda *args, **kwargs: {  # type: ignore[method-assign]
        "error": {"code": 400, "status": "INVALID_ARGUMENT"}
    }
    with pytest.raises(YouTubeAPIError):
        client.create_playlist("Target")


def test_create_playlist_validates_input(client):
    with pytest.raises(ValueError):
        client.create_playlist("   ")
    with pytest.raises(ValueError):
        client.create_playlist("Target", privacy="secret")


def test_add_playlist_items_batches_and_reports_progress(client):
    calls = []

    def fake_add(playlist_id, video_ids):
        calls.append((playlist_id, list(video_ids)))
        return {"status": "STATUS_SUCCEEDED"}

    client._ytm.add_playlist_items = fake_add  # type: ignore[method-assign]
    progress = []
    added = client.add_playlist_items(
        "PL1",
        ["v0", "v1", "v2", "v3", "v4"],
        batch_size=2,
        progress=lambda done, total: progress.append((done, total)),
    )

    assert added == 5
    assert calls == [
        ("PL1", ["v0", "v1"]),
        ("PL1", ["v2", "v3"]),
        ("PL1", ["v4"]),
    ]
    assert progress == [(2, 5), (4, 5), (5, 5)]


def test_add_playlist_items_raises_on_failed_status(client):
    client._ytm.add_playlist_items = lambda *args, **kwargs: {  # type: ignore[method-assign]
        "status": "STATUS_FAILED"
    }
    with pytest.raises(YouTubeAPIError):
        client.add_playlist_items("PL1", ["v0"])


def test_add_playlist_items_retries_transient_errors(client, monkeypatch):
    monkeypatch.setattr("spyt.youtube.client.time.sleep", lambda seconds: None)
    calls = []

    def flaky_add(playlist_id, video_ids):
        calls.append(playlist_id)
        if len(calls) == 1:
            raise YTMusicServerError("Server returned HTTP 503: Service Unavailable")
        return {"status": "STATUS_SUCCEEDED"}

    client._ytm.add_playlist_items = flaky_add  # type: ignore[method-assign]
    added = client.add_playlist_items("PL1", ["v0"])

    assert added == 1
    assert len(calls) == 2
