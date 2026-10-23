from __future__ import annotations

import time

from fastapi.testclient import TestClient

from spyt.spotify.models import Album, Artist
from spyt.spotify.models import Playlist as SpotifyPlaylist
from spyt.spotify.models import Track as SpotifyTrack
from spyt.transfer import MatchedTrack, build_report
from spyt.web.app import create_app
from spyt.web.jobs import JobManager, TransferRequest

COOKIES = "sp_dc=" + "a" * 60
HEADERS = (
    "Cookie: SID=abc; __Secure-3PAPISID=def\n"
    "Origin: https://music.youtube.com\n"
    "X-Goog-AuthUser: 0"
)


class StubManager:
    def __init__(self) -> None:
        self.requests: list[TransferRequest] = []
        self.jobs = {}

    def start(self, request):
        self.requests.append(request)

        class StubJob:
            id = "job123"

        return StubJob()

    def get(self, job_id):
        return self.jobs.get(job_id)


def test_index_and_static_assets():
    client = TestClient(create_app(StubManager()))
    index = client.get("/")
    assert index.status_code == 200
    assert "Spotify playlist" in index.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/favicon.svg").status_code == 200
    assert client.get("/api/health").json() == {"status": "ok"}


def test_invalid_playlist_is_rejected():
    client = TestClient(create_app(StubManager()))
    response = client.post(
        "/api/jobs",
        json={"playlist": "not a playlist", "cookies": COOKIES, "preview": True},
    )
    assert response.status_code == 400
    assert "Invalid playlist" in response.json()["detail"]


def test_invalid_cookies_are_rejected():
    client = TestClient(create_app(StubManager()))
    response = client.post(
        "/api/jobs",
        json={"playlist": "pl1234567890", "cookies": "hello world", "preview": True},
    )
    assert response.status_code == 400
    assert "cookies" in response.json()["detail"].lower()


def test_headers_required_unless_preview():
    client = TestClient(create_app(StubManager()))
    response = client.post(
        "/api/jobs",
        json={
            "playlist": "pl1234567890",
            "cookies": COOKIES,
        },
    )
    assert response.status_code == 400
    assert "headers are required" in response.json()["detail"]


def test_preview_needs_no_headers_and_starts_job():
    manager = StubManager()
    client = TestClient(create_app(manager))
    response = client.post(
        "/api/jobs",
        json={
            "playlist": "pl1234567890",
            "cookies": COOKIES,
            "preview": True,
            "max_items": 5,
        },
    )
    assert response.status_code == 200
    assert response.json() == {"id": "job123"}
    assert len(manager.requests) == 1
    request = manager.requests[0]
    assert request.preview is True
    assert request.headers is None
    assert request.max_items == 5
    assert request.playlist == "pl1234567890"


def test_invalid_headers_are_rejected():
    client = TestClient(create_app(StubManager()))
    response = client.post(
        "/api/jobs",
        json={
            "playlist": "pl1234567890",
            "cookies": COOKIES,
            "headers": "just some words",
        },
    )
    assert response.status_code == 400
    assert "headers" in response.json()["detail"].lower()


def test_valid_transfer_starts_job():
    manager = StubManager()
    client = TestClient(create_app(manager))
    response = client.post(
        "/api/jobs",
        json={
            "playlist": "pl1234567890",
            "cookies": COOKIES,
            "headers": HEADERS,
            "name": " My playlist ",
            "privacy": "unlisted",
        },
    )
    assert response.status_code == 200
    assert manager.requests[0].name == "My playlist"
    assert manager.requests[0].privacy == "unlisted"


def test_unknown_job_is_404():
    client = TestClient(create_app(StubManager()))
    assert client.get("/api/jobs/missing").status_code == 404
    assert client.get("/api/jobs/missing/events").status_code == 404
    assert client.post("/api/jobs/missing/cancel").status_code == 404
    assert client.get("/api/jobs/missing/report").status_code == 404


def _playlist() -> SpotifyPlaylist:
    return SpotifyPlaylist(
        id="pl1",
        uri="spotify:playlist:pl1",
        name="Test",
        total_items=1,
        tracks=[
            SpotifyTrack(
                position=1,
                uri="spotify:track:t1",
                id="t1",
                name="Song",
                kind="track",
                artists=[Artist(name="Artist")],
                album=Album(name="Album"),
                duration_ms=180000,
            )
        ],
    )


def _fake_runner(job, request):
    job.update(status="running", stage="matching", message="Matching")
    job.emit(
        "track",
        {
            "position": 1,
            "status": "matched",
            "spotify": {"name": "Song", "artists": ["Artist"]},
            "youtube": {"video_id": "v1", "title": "Song"},
            "score": 0.98,
            "reason": None,
            "duplicate_of": None,
        },
    )
    report = build_report(
        _playlist(),
        [
            MatchedTrack(
                position=1,
                spotify={"id": "t1", "name": "Song", "artists": ["Artist"]},
                youtube={"video_id": "v1", "title": "Song"},
                status="matched",
                score=0.98,
            )
        ],
        target={
            "platform": "youtube_music",
            "playlist_id": "PLNEW",
            "url": "https://music.youtube.com/playlist?list=PLNEW",
            "name": "Test",
            "privacy": "PRIVATE",
            "added": 1,
        },
        status="created",
    )
    job.playlist = _playlist()
    job.report = report
    job.update(
        status="done",
        stage="done",
        message="Created",
        counts=report.summary,
        target=report.target,
    )


def test_snapshot_sse_and_report_download():
    manager = JobManager(runner=_fake_runner)
    client = TestClient(create_app(manager))
    job = manager.start(
        TransferRequest(
            playlist="pl1234567890",
            cookies=COOKIES,
            headers=HEADERS,
            preview=True,
        )
    )

    for _ in range(100):
        if job.status == "done":
            break
        time.sleep(0.01)

    snapshot = client.get(f"/api/jobs/{job.id}").json()
    assert snapshot["status"] == "done"
    assert snapshot["target"]["playlist_id"] == "PLNEW"
    assert "cookies" not in snapshot

    with client.stream("GET", f"/api/jobs/{job.id}/events?after=0") as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    assert "event: track" in body
    assert "event: state" in body
    assert '"status": "done"' in body

    report = client.get(f"/api/jobs/{job.id}/report")
    assert report.status_code == 200
    assert report.headers["content-disposition"].startswith("attachment;")
    assert report.json()["summary"]["matched"] == 1
