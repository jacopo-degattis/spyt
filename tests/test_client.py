import pytest

from spoyt.spotify.client import SpotifyWebClient

PLAYLIST_ID = "6yNRwWCVVNLEsif1esJ1Cc"


def _client_with_pages(pages, calls):
    client = SpotifyWebClient(access_token="test-token")

    def fake_fetch_page(playlist_id, *, offset, limit):
        calls.append((playlist_id, offset, limit))
        return pages[offset]

    client._fetch_page = fake_fetch_page  # type: ignore[method-assign]
    return client


def test_pagination_and_episode_filtering(payload_factory, track_factory, episode_factory):
    calls = []
    pages = {
        0: payload_factory(
            [
                track_factory(track_id="t1", name="One"),
                track_factory(track_id="t2", name="Two"),
                episode_factory(episode_id="e1"),
            ],
            total=4,
        ),
        3: payload_factory([track_factory(track_id="t4", name="Four")], total=4),
    }
    client = _client_with_pages(pages, calls)

    playlist = client.fetch_playlist(PLAYLIST_ID, page_size=3)

    assert calls == [(PLAYLIST_ID, 0, 3), (PLAYLIST_ID, 3, 1)]
    assert [track.position for track in playlist.tracks] == [1, 2, 4]
    assert playlist.total_items == 4
    assert len(playlist.skipped) == 1
    assert playlist.skipped[0].reason == "episode"
    assert playlist.skipped[0].position == 3
    assert playlist.fetched_at is not None


def test_include_episodes(payload_factory, track_factory, episode_factory):
    pages = {
        0: payload_factory(
            [track_factory(track_id="t1", name="One"), episode_factory(episode_id="e1")],
            total=2,
        )
    }
    client = _client_with_pages(pages, calls=[])

    playlist = client.fetch_playlist(PLAYLIST_ID, page_size=10, include_episodes=True)

    assert len(playlist.tracks) == 2
    assert {track.kind for track in playlist.tracks} == {"track", "episode"}
    assert playlist.skipped == []


def test_max_items_limits_page_size(payload_factory, track_factory):
    calls = []
    pages = {
        0: payload_factory(
            [track_factory(track_id="t1", name="One"), track_factory(track_id="t2", name="Two")],
            total=100,
        )
    }
    client = _client_with_pages(pages, calls)

    playlist = client.fetch_playlist(PLAYLIST_ID, page_size=250, max_items=2)

    assert calls == [(PLAYLIST_ID, 0, 2)]
    assert len(playlist.tracks) == 2


def test_invalid_page_size():
    client = SpotifyWebClient(access_token="test-token")
    with pytest.raises(ValueError):
        client.fetch_playlist(PLAYLIST_ID, page_size=0)
    with pytest.raises(ValueError):
        client.fetch_playlist(PLAYLIST_ID, page_size=5001)
