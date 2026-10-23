from spyt.youtube.mapping import map_playlist

PLAYLIST_ID = "PLQwVIlKxHM6qv-o99iX9R85og7IzF9YS_"


def _raw_playlist() -> dict:
    return {
        "id": PLAYLIST_ID,
        "title": "My Playlist",
        "description": "Some description",
        "privacy": "PRIVATE",
        "year": "2024",
        "trackCount": 3,
        "duration": "12 minutes",
        "duration_seconds": 720,
        "author": {"name": "Tester", "id": "UC-author"},
        "thumbnails": [
            {"url": "https://img/small.jpg", "width": 64, "height": 64},
            {"url": "https://img/large.jpg", "width": 1200, "height": 1200},
        ],
        "tracks": [
            {
                "videoId": "video1",
                "setVideoId": "set1",
                "title": "Song One",
                "artists": [{"name": "Artist One", "id": "UC-artist"}],
                "album": {"name": "Album One", "id": "MPREb_album"},
                "duration": "3:20",
                "duration_seconds": 200,
                "isExplicit": True,
                "isAvailable": True,
                "videoType": "MUSIC_VIDEO_TYPE_ATV",
                "thumbnails": [
                    {"url": "https://img/track-small.jpg", "width": 60, "height": 60},
                    {"url": "https://img/track-large.jpg", "width": 120, "height": 120},
                ],
            },
            {
                "videoId": "episode1",
                "title": "Episode One",
                "duration": "45:00",
                "duration_seconds": 2700,
                "isAvailable": True,
                "videoType": "MUSIC_VIDEO_TYPE_PODCAST_EPISODE",
                "thumbnails": [],
            },
            {
                "videoId": "video3",
                "title": "Video Three",
                "artists": [{"name": "Artist Three", "id": None}],
                "album": None,
                "duration": "4:00",
                "duration_seconds": 240,
                "isExplicit": False,
                "isAvailable": False,
                "videoType": "MUSIC_VIDEO_TYPE_OMV",
                "thumbnails": [],
            },
        ],
    }


def test_maps_playlist_metadata():
    playlist = map_playlist(_raw_playlist(), playlist_id=PLAYLIST_ID)

    assert playlist.id == PLAYLIST_ID
    assert playlist.url == f"https://music.youtube.com/playlist?list={PLAYLIST_ID}"
    assert playlist.title == "My Playlist"
    assert playlist.description == "Some description"
    assert playlist.privacy == "PRIVATE"
    assert playlist.year == "2024"
    assert playlist.cover_url == "https://img/large.jpg"
    assert playlist.reported_track_count == 3
    assert playlist.duration_text == "12 minutes"
    assert playlist.duration_seconds == 720
    assert playlist.owner is not None
    assert playlist.owner.name == "Tester"
    assert playlist.to_dict()["track_count"] == 2


def test_maps_tracks_and_skips_episode():
    playlist = map_playlist(_raw_playlist(), playlist_id=PLAYLIST_ID)
    first, third = playlist.tracks

    assert first.position == 1
    assert first.video_id == "video1"
    assert first.set_video_id == "set1"
    assert first.title == "Song One"
    assert first.kind == "song"
    assert first.artists[0].name == "Artist One"
    assert first.album is not None
    assert first.album.name == "Album One"
    assert first.duration_ms == 200000
    assert first.duration_text == "3:20"
    assert first.explicit is True
    assert first.available is True
    assert first.cover_url == "https://img/track-large.jpg"

    assert third.position == 3
    assert third.kind == "video"
    assert third.available is False

    assert len(playlist.skipped) == 1
    assert playlist.skipped[0].position == 2
    assert playlist.skipped[0].reason == "episode"


def test_include_episodes():
    playlist = map_playlist(
        _raw_playlist(), playlist_id=PLAYLIST_ID, include_episodes=True
    )
    assert [track.kind for track in playlist.tracks] == ["song", "episode", "video"]
    assert playlist.skipped == []


def test_max_items_caps_positions():
    playlist = map_playlist(_raw_playlist(), playlist_id=PLAYLIST_ID, max_items=2)
    assert [track.position for track in playlist.tracks] == [1]
    assert playlist.skipped[0].position == 2


def test_deleted_item_is_skipped():
    raw = _raw_playlist()
    raw["tracks"] = [{"title": "Song deleted"}]
    playlist = map_playlist(raw, playlist_id=PLAYLIST_ID)
    assert playlist.tracks == []
    assert playlist.skipped[0].reason == "deleted"
