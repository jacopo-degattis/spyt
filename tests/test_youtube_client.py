import pytest
from ytmusicapi.exceptions import YTMusicServerError

from spyt.errors import YouTubeAuthError, YouTubePlaylistNotFoundError
from spyt.youtube.client import YouTubeMusicClient

PLAYLIST_ID = "PLQwVIlKxHM6qv-o99iX9R85og7IzF9YS_"
RAW = {
    "id": PLAYLIST_ID,
    "title": "Test",
    "tracks": [
        {
            "videoId": "video1",
            "title": "Song",
            "artists": [],
            "album": None,
            "duration_seconds": 100,
            "isAvailable": True,
            "videoType": "MUSIC_VIDEO_TYPE_ATV",
            "thumbnails": [],
        }
    ],
}


def _client_with_raw(raw=RAW, calls=None):
    calls = calls if calls is not None else []
    client = YouTubeMusicClient()

    def fake_get_playlist(playlist_id, limit=None):
        calls.append((playlist_id, limit))
        return raw

    client._ytm.get_playlist = fake_get_playlist  # type: ignore[method-assign]
    return client, calls


def test_fetch_playlist_maps_response():
    client, calls = _client_with_raw()
    playlist = client.fetch_playlist(f"VL{PLAYLIST_ID}")
    assert calls == [(PLAYLIST_ID, None)]
    assert playlist.title == "Test"
    assert len(playlist.tracks) == 1


def test_small_max_items_disables_continuations():
    client, calls = _client_with_raw()
    client.fetch_playlist(PLAYLIST_ID, max_items=50)
    assert calls == [(PLAYLIST_ID, 0)]


def test_large_max_items_is_forwarded():
    client, calls = _client_with_raw()
    client.fetch_playlist(PLAYLIST_ID, max_items=150)
    # The first page is always 100 items, so only 50 more are requested.
    assert calls == [(PLAYLIST_ID, 50)]


def test_unauthorized_is_mapped():
    client, _ = _client_with_raw()

    def fail(playlist_id, limit=None):
        raise YTMusicServerError("Server returned HTTP 401: Unauthorized")

    client._ytm.get_playlist = fail  # type: ignore[method-assign]
    with pytest.raises(YouTubeAuthError):
        client.fetch_playlist(PLAYLIST_ID)


def test_missing_page_is_mapped_to_not_found():
    client, _ = _client_with_raw()

    def fail(playlist_id, limit=None):
        raise KeyError("Unable to find 'contents'")

    client._ytm.get_playlist = fail  # type: ignore[method-assign]
    with pytest.raises(YouTubePlaylistNotFoundError):
        client.fetch_playlist(PLAYLIST_ID)
