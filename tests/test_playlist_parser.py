import pytest

from spoyt.errors import PlaylistNotFoundError, SpotifyAPIError
from spoyt.spotify.models import SkippedItem, Track
from spoyt.spotify.playlist import parse_playlist_page

PLAYLIST_ID = "6yNRwWCVVNLEsif1esJ1Cc"


def test_parses_header_and_track(payload_factory, track_factory):
    payload = payload_factory([track_factory(track_id="track1", name="Song", explicit=True)])
    page = parse_playlist_page(payload, playlist_id=PLAYLIST_ID)

    assert page.total_items == 1
    assert page.playlist.name == "Test Playlist"
    assert page.playlist.cover_url == "https://img/playlist.jpg"
    assert page.playlist.owner is not None
    assert page.playlist.owner.name == "Tester"

    track = page.items[0]
    assert isinstance(track, Track)
    assert track.position == 1
    assert track.id == "track1"
    assert track.uri == "spotify:track:track1"
    assert track.name == "Song"
    assert track.artists[0].name == "Artist"
    assert track.album is not None
    assert track.album.cover_url == "https://img/large.jpg"
    assert track.album.release_date == "2020-05-01T00:00:00Z"
    assert track.duration_ms == 180000
    assert track.explicit is True
    assert track.playcount == 42
    assert track.added_at == "2024-01-02T03:04:05.000Z"
    assert track.added_by == "Tester"


def test_parses_episode(payload_factory, episode_factory):
    payload = payload_factory([episode_factory(episode_id="ep1")])
    page = parse_playlist_page(payload, playlist_id=PLAYLIST_ID)

    episode = page.items[0]
    assert isinstance(episode, Track)
    assert episode.kind == "episode"
    assert episode.duration_ms == 60000
    assert episode.album is not None
    assert episode.album.name == "A Podcast"
    assert episode.album.cover_url == "https://img/show.jpg"


def test_unavailable_item_becomes_skipped(payload_factory):
    payload = payload_factory([{"itemV2": {"data": None}}])
    page = parse_playlist_page(payload, playlist_id=PLAYLIST_ID)

    assert page.items == [SkippedItem(position=1, reason="unavailable")]


def test_item_v3_fallback(payload_factory):
    item = {
        "itemV2": None,
        "itemV3": {
            "data": {
                "uri": "spotify:track:v3track",
                "entityTypeTrait": {"type": "ENTITY_TYPE_TRACK"},
                "identityTrait": {
                    "name": "Fallback Song",
                    "contributors": {
                        "items": [{"name": "Fallback Artist", "uri": "spotify:artist:1"}]
                    },
                    "contentHierarchyParent": {
                        "uri": "spotify:album:2",
                        "identityTrait": {"name": "Fallback Album"},
                        "publishingMetadataTrait": {
                            "firstPublishedAt": {"isoString": "2001-01-01"}
                        },
                    },
                },
                "consumptionExperienceTrait": {
                    "duration": {"seconds": 200},
                    "contentRatings": [{"label": "EXPLICIT"}],
                },
                "playability": {"playable": False},
                "visualIdentityTrait": {
                    "squareCoverImage": {
                        "sources": [{"url": "https://img/v3.jpg", "width": 300, "height": 300}]
                    }
                },
            }
        },
    }
    page = parse_playlist_page(payload_factory([item]), playlist_id=PLAYLIST_ID)

    track = page.items[0]
    assert isinstance(track, Track)
    assert track.name == "Fallback Song"
    assert track.artists[0].name == "Fallback Artist"
    assert track.album.name == "Fallback Album"
    assert track.album.cover_url == "https://img/v3.jpg"
    assert track.duration_ms == 200000
    assert track.explicit is True
    assert track.playable is False


def test_positions_use_offset(payload_factory, track_factory):
    payload = payload_factory(
        [track_factory(track_id="one", name="One"), track_factory(track_id="two", name="Two")],
        total=10,
    )
    page = parse_playlist_page(payload, playlist_id=PLAYLIST_ID, offset=5)
    assert [item.position for item in page.items] == [6, 7]
    assert page.total_items == 10


def test_not_found_payload(payload_factory):
    payload = payload_factory([], typename="NotFound", message="Object not found")
    with pytest.raises(PlaylistNotFoundError):
        parse_playlist_page(payload, playlist_id=PLAYLIST_ID)


def test_generic_error_payload(payload_factory):
    payload = payload_factory([], typename="GenericError", message="Something broke")
    with pytest.raises(SpotifyAPIError, match="Something broke"):
        parse_playlist_page(payload, playlist_id=PLAYLIST_ID)
