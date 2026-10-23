from __future__ import annotations

from spyt.spotify.models import Album, Artist, Owner, Track
from spyt.spotify.models import Playlist as SpotifyPlaylist
from spyt.transfer import (
    MatchedTrack,
    build_report,
    create_target_playlist,
    load_report,
    mark_duplicates,
    match_playlist,
    resume_index,
    spotify_track_document,
    write_report,
)
from spyt.youtube.models import Artist as YouTubeArtist
from spyt.youtube.models import SearchTrack


def _spotify_playlist(count: int = 3) -> SpotifyPlaylist:
    tracks = [
        Track(
            position=index + 1,
            uri=f"spotify:track:t{index}",
            id=f"t{index}",
            name=f"Song {index}",
            kind="track",
            artists=[Artist(name="Artist")],
            album=Album(name="Album"),
            duration_ms=180000,
        )
        for index in range(count)
    ]
    return SpotifyPlaylist(
        id="playlist1",
        uri="spotify:playlist:playlist1",
        name="My Playlist",
        description="description",
        owner=Owner(name="Tester"),
        total_items=count,
        tracks=tracks,
    )


def _result(video_id: str) -> SearchTrack:
    return SearchTrack(
        video_id=video_id,
        title="Song",
        kind="song",
        artists=[YouTubeArtist(name="Artist")],
        duration_seconds=180,
    )


def _search_mapping(mapping):
    def search(query, *, filter="songs", limit=8):
        return mapping.get(query, [])

    return search


def test_match_playlist_matches_and_skips():
    playlist = _spotify_playlist(3)
    playlist.tracks[1].playable = False

    def search(query, *, filter="songs", limit=8):
        video_id = {"Song 0 Artist": "v0", "Song 2 Artist": "v2"}.get(query)
        return [_result(video_id)] if video_id else []

    progress = []
    tracks = match_playlist(
        playlist,
        search,
        progress=lambda done, total: progress.append((done, total)),
    )
    assert [track.status for track in tracks] == ["matched", "skipped", "matched"]
    assert tracks[1].reason == "unavailable"
    assert [track.youtube["video_id"] for track in tracks if track.youtube] == ["v0", "v2"]
    assert progress == [(1, 3), (2, 3), (3, 3)]


def test_match_playlist_respects_max_items():
    playlist = _spotify_playlist(5)

    def search(query, *, filter="songs", limit=8):
        return [_result("v")]

    tracks = match_playlist(playlist, search, max_items=2)
    assert len(tracks) == 2


def test_mark_duplicates_keeps_first_match():
    tracks = [
        MatchedTrack(1, {}, {"video_id": "same"}, "matched"),
        MatchedTrack(2, {}, {"video_id": "other"}, "matched"),
        MatchedTrack(3, {}, {"video_id": "same"}, "matched"),
    ]
    duplicates = mark_duplicates(tracks)
    assert duplicates == 1
    assert [track.status for track in tracks] == ["matched", "matched", "duplicate"]
    assert tracks[2].duplicate_of == 1


def test_resume_reuses_matched_and_skipped_but_retries_unmatched():
    playlist = _spotify_playlist(3)
    resume = {
        1: {
            "position": 1,
            "spotify": {"id": "t0"},
            "youtube": {"video_id": "cached0"},
            "status": "matched",
            "reason": None,
            "score": 0.95,
            "query": "cached query",
        },
        2: {
            "position": 2,
            "spotify": {"id": "t1"},
            "youtube": None,
            "status": "skipped",
            "reason": "unavailable",
        },
        3: {
            "position": 3,
            "spotify": {"id": "t2"},
            "youtube": None,
            "status": "unmatched",
            "reason": "no results",
        },
    }
    calls = []

    def search(query, *, filter="songs", limit=8):
        calls.append(query)
        return [_result("fresh2")]

    tracks = match_playlist(playlist, search, resume=resume)
    assert tracks[0].youtube["video_id"] == "cached0"
    assert tracks[1].status == "skipped"
    assert tracks[2].youtube["video_id"] == "fresh2"
    assert calls == ["Song 2 Artist"]


def test_resume_rejects_cache_for_a_different_spotify_id():
    playlist = _spotify_playlist(1)
    resume = {
        1: {
            "position": 1,
            "spotify": {"id": "different"},
            "youtube": {"video_id": "cached0"},
            "status": "matched",
        }
    }

    def search(query, *, filter="songs", limit=8):
        return [_result("fresh")]

    tracks = match_playlist(playlist, search, resume=resume)
    assert tracks[0].youtube["video_id"] == "fresh"


def test_rematch_ignores_cached_matches():
    playlist = _spotify_playlist(1)
    resume = {
        1: {
            "position": 1,
            "spotify": {"id": "t0"},
            "youtube": {"video_id": "cached"},
            "status": "matched",
        }
    }

    def search(query, *, filter="songs", limit=8):
        return [_result("fresh")]

    tracks = match_playlist(playlist, search, resume=resume, rematch=True)
    assert tracks[0].youtube["video_id"] == "fresh"


class FakeClient:
    def __init__(self) -> None:
        self.created = []
        self.added = []

    def create_playlist(self, title, *, description="", privacy="PRIVATE"):
        self.created.append((title, description, privacy))
        return "PLNEW"

    def add_playlist_items(self, playlist_id, video_ids, *, progress=None):
        ids = list(video_ids)
        self.added.append((playlist_id, ids))
        if progress:
            progress(len(ids), len(ids))
        return len(ids)


def test_create_target_playlist_adds_only_matched_tracks():
    playlist = _spotify_playlist(3)
    tracks = [
        MatchedTrack(1, {}, {"video_id": "v1"}, "matched"),
        MatchedTrack(2, {}, None, "unmatched"),
        MatchedTrack(3, {}, {"video_id": "v3"}, "matched"),
    ]
    report = build_report(playlist, tracks)
    progress = []

    added = create_target_playlist(
        FakeClient(),
        report,
        title="Target",
        description="desc",
        privacy="unlisted",
        progress=lambda done, total: progress.append((done, total)),
    )

    assert added == 2
    assert report.status == "created"
    assert report.target["playlist_id"] == "PLNEW"
    assert report.target["url"].endswith("list=PLNEW")
    assert report.target["added"] == 2
    assert progress == [(2, 2)]
    assert report.summary["matched"] == 2
    assert report.summary["added"] == 2


def test_create_target_playlist_requires_matches():
    report = build_report(_spotify_playlist(1), [MatchedTrack(1, {}, None, "unmatched")])
    try:
        create_target_playlist(FakeClient(), report, title="Target")
    except ValueError as exc:
        assert "no matched tracks" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_report_roundtrip_and_resume_index(tmp_path):
    playlist = _spotify_playlist(2)
    tracks = [
        MatchedTrack(1, spotify_track_document(playlist.tracks[0]), {"video_id": "v1"}, "matched", score=0.9),
        MatchedTrack(2, spotify_track_document(playlist.tracks[1]), None, "unmatched", reason="no results"),
    ]
    report = build_report(playlist, tracks)
    path = write_report(report, tmp_path / "report.json")

    loaded = load_report(path)
    assert loaded is not None
    assert loaded["spotify"]["id"] == "playlist1"
    assert loaded["summary"]["matched"] == 1
    assert loaded["summary"]["unmatched"] == 1

    index = resume_index(loaded)
    assert set(index) == {1, 2}


def test_load_report_ignores_missing_or_invalid_files(tmp_path):
    assert load_report(tmp_path / "missing.json") is None
    broken = tmp_path / "broken.json"
    broken.write_text("{")
    assert load_report(broken) is None
