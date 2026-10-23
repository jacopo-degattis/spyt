from __future__ import annotations

import time

import pytest

from spyt.errors import SpotifyAPIError
from spyt.spotify.models import Album, Artist, Owner
from spyt.spotify.models import Playlist as SpotifyPlaylist
from spyt.spotify.models import Track as SpotifyTrack
from spyt.web import jobs as jobs_module
from spyt.web.jobs import (
    Job,
    JobCancelled,
    JobManager,
    TransferRequest,
    run_job,
)
from spyt.youtube.models import Artist as YouTubeArtist
from spyt.youtube.models import SearchTrack


def make_playlist(count: int = 2) -> SpotifyPlaylist:
    tracks = [
        SpotifyTrack(
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
        id="pl1",
        uri="spotify:playlist:pl1",
        name="Test Playlist",
        description="desc",
        owner=Owner(name="Tester"),
        total_items=count,
        tracks=tracks,
    )


class FakeSpotify:
    def __init__(self, playlist, *, error: Exception | None = None) -> None:
        self.playlist = playlist
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def fetch_playlist(self, reference, *, page_size=250, progress=None):
        if self.error:
            raise self.error
        if progress:
            progress(self.playlist.total_items, self.playlist.total_items)
        return self.playlist


class FakeYouTube:
    def __init__(self) -> None:
        self.created = []
        self.added = []

    def search_tracks(self, query, *, filter="songs", limit=8):
        title = query.split(" Artist")[0]
        return [
            SearchTrack(
                video_id=f"video-{title}",
                title=title,
                kind="song",
                artists=[YouTubeArtist(name="Artist")],
                duration_seconds=180,
            )
        ]

    def create_playlist(self, title, *, description="", privacy="PRIVATE"):
        self.created.append((title, description, privacy))
        return "PLNEW"

    def add_playlist_items(self, playlist_id, video_ids, *, progress=None):
        ids = list(video_ids)
        self.added.append((playlist_id, ids))
        if progress:
            progress(len(ids), len(ids))
        return len(ids)


@pytest.fixture
def fake_transfer(monkeypatch):
    playlist = make_playlist(2)
    youtube = FakeYouTube()
    monkeypatch.setattr(jobs_module, "parse_cookies", lambda text: {"sp_dc": "x"})
    monkeypatch.setattr(jobs_module, "parse_headers", lambda text: {"cookie": "x"})
    monkeypatch.setattr(
        jobs_module,
        "SpotifyWebClient",
        lambda cookies: FakeSpotify(playlist),
    )
    monkeypatch.setattr(
        jobs_module,
        "YouTubeMusicClient",
        lambda headers=None, verify_session=True: youtube,
    )
    return playlist, youtube


def make_request(**overrides) -> TransferRequest:
    options = {
        "playlist": "https://open.spotify.com/playlist/pl1",
        "cookies": "sp_dc=value",
        "headers": "Cookie: value",
        "name": "Target",
        "preview": False,
    }
    options.update(overrides)
    return TransferRequest(**options)


def test_job_snapshot_never_contains_credentials():
    manager = JobManager(runner=lambda job, request: None)
    job = manager.start(make_request())
    time.sleep(0.05)

    snapshot = job.snapshot()
    assert "cookies" not in snapshot
    assert "headers" not in snapshot
    assert snapshot["options"]["name"] == "Target"
    assert job.headers is None


def test_job_events_replay_and_wait():
    job = Job(make_request())
    job.update(status="running", stage="spotify", message="Reading")

    events = job.events_after(0)
    assert len(events) == 1
    assert events[0]["type"] == "state"
    assert events[0]["data"]["status"] == "running"
    assert job.events_after(events[0]["id"]) == []

    job.emit("track", {"position": 1})
    assert job.wait_for_events(events[0]["id"], timeout=0.1) is True
    assert job.events_after(events[0]["id"])[0]["type"] == "track"


def test_run_job_preview_matches_without_creating(fake_transfer):
    _playlist, youtube = fake_transfer
    job = Job(make_request(preview=True))

    run_job(job, make_request(preview=True))

    assert job.status == "ready"
    assert job.stage == "ready"
    assert job.report is not None
    assert job.target is None
    assert youtube.created == []
    assert job.counts["matched"] == 2
    track_events = [e for e in job.events_after(0) if e["type"] == "track"]
    assert [event["data"]["position"] for event in track_events] == [1, 2]


def test_run_job_creates_playlist_and_adds_tracks(fake_transfer):
    _, youtube = fake_transfer
    request = make_request()
    job = Job(request)

    run_job(job, request)

    assert job.status == "done"
    assert job.report is not None
    assert job.report.status == "created"
    assert job.target["playlist_id"] == "PLNEW"
    assert job.target["added"] == 2
    assert youtube.created[0][0] == "Target"
    assert youtube.added == [("PLNEW", ["video-Song 0", "video-Song 1"])]
    assert job.counts["added"] == 2


def test_run_job_marks_duplicate_matches(fake_transfer):
    _, youtube = fake_transfer
    youtube.search_tracks = lambda query, *, filter="songs", limit=8: [  # type: ignore[method-assign]
        SearchTrack(
            video_id="same-video",
            title=query.split(" Artist")[0],
            kind="song",
            artists=[YouTubeArtist(name="Artist")],
            duration_seconds=180,
        )
    ]
    request = make_request(preview=True)
    job = Job(request)

    run_job(job, request)

    assert job.counts["matched"] == 1
    assert job.counts["duplicate"] == 1
    assert job.report is not None
    statuses = [track.status for track in job.report.tracks]
    assert statuses == ["matched", "duplicate"]


def test_run_job_reports_spotify_errors(monkeypatch):
    monkeypatch.setattr(jobs_module, "parse_cookies", lambda text: {})
    monkeypatch.setattr(
        jobs_module,
        "SpotifyWebClient",
        lambda cookies: FakeSpotify(make_playlist(1), error=SpotifyAPIError("boom")),
    )
    request = make_request(preview=True)
    job = Job(request)

    run_job(job, request)

    assert job.status == "failed"
    assert job.stage == "spotify"
    assert job.error == "boom"


def test_cancelled_job_stops_before_matching(monkeypatch):
    playlist = make_playlist(2)
    monkeypatch.setattr(jobs_module, "parse_cookies", lambda text: {})
    monkeypatch.setattr(jobs_module, "parse_headers", lambda text: {})

    class CancellingSpotify(FakeSpotify):
        def fetch_playlist(self, reference, *, page_size=250, progress=None):
            if progress:
                progress(0, self.playlist.total_items)
            return self.playlist

    class CancellingYouTube(FakeYouTube):
        def search_tracks(self, query, *, filter="songs", limit=8):
            raise JobCancelled

    monkeypatch.setattr(
        jobs_module, "SpotifyWebClient", lambda cookies: CancellingSpotify(playlist)
    )
    monkeypatch.setattr(
        jobs_module,
        "YouTubeMusicClient",
        lambda headers=None, verify_session=True: CancellingYouTube(),
    )
    request = make_request(preview=True)
    job = Job(request)

    run_job(job, request)

    assert job.status == "cancelled"
    assert job.stage == "matching"


def test_manager_clears_credentials_when_done():
    def runner(job, request):
        job.headers = {"cookie": "secret"}
        job.update(status="done", stage="done", message="done")

    job = JobManager(runner=runner).start(make_request())
    for _ in range(50):
        if job.status == "done":
            break
        time.sleep(0.01)
    assert job.status == "done"
    assert job.headers is None
