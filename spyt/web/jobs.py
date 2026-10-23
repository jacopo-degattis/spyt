"""In-memory transfer jobs backing the local web UI.

Jobs never leave the process and hold credentials only for the lifetime of the
transfer. Each job exposes a small, replayable event log that the browser
consumes over Server-Sent Events.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..errors import SpytError
from ..match import DEFAULT_MIN_SCORE
from ..spotify import SpotifyWebClient, parse_cookies
from ..transfer import (
    MatchedTrack,
    TransferReport,
    build_report,
    create_target_playlist,
    mark_duplicate,
    match_playlist,
)
from ..youtube import YouTubeMusicClient, parse_headers

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"done", "failed", "cancelled"}
STREAM_END_STATUSES = TERMINAL_STATUSES | {"ready"}
JOB_TTL_SECONDS = 2 * 60 * 60


class JobCancelled(Exception):
    """Raised inside a worker when the user cancels the transfer."""


class JobStateError(Exception):
    """The requested transition is not valid for the job's current state."""


@dataclass
class TransferRequest:
    playlist: str
    cookies: str
    headers: str | None = None
    name: str | None = None
    description: str | None = None
    privacy: str = "private"
    max_items: int | None = None
    min_score: float = DEFAULT_MIN_SCORE
    preview: bool = False


@dataclass
class CreateOptions:
    name: str | None = None
    description: str | None = None
    privacy: str | None = None


class Job:
    def __init__(self, request: TransferRequest) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.created_at = time.time()
        self.preview = request.preview
        self.options = {
            "name": request.name,
            "privacy": request.privacy,
            "max_items": request.max_items,
            "min_score": request.min_score,
            "preview": request.preview,
        }

        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._events: list[dict[str, Any]] = []
        self._next_event_id = 1
        self._creating = False

        self.status = "queued"
        self.stage = "queued"
        self.message = "Waiting to start"
        self.progress: dict[str, int | None] = {"done": 0, "total": None}
        self.counts = {
            "matched": 0,
            "unmatched": 0,
            "skipped": 0,
            "duplicate": 0,
            "added": 0,
        }
        self.spotify: dict[str, Any] | None = None
        self.target: dict[str, Any] | None = None
        self.error: str | None = None

        self.cancel_event = threading.Event()
        self.playlist: Any = None
        self.matches: list[MatchedTrack] = []
        self.report: TransferReport | None = None
        self.headers: dict[str, str] | None = None
        self.total_tracks: int | None = None

    # -- state ---------------------------------------------------------
    def update(self, **fields: Any) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(self, key, value)
            self._emit_locked("state", self._snapshot_locked())

    def emit(self, event_type: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._emit_locked(event_type, data)

    def _emit_locked(self, event_type: str, data: dict[str, Any]) -> None:
        self._events.append(
            {"id": self._next_event_id, "type": event_type, "data": data}
        )
        self._next_event_id += 1
        self._condition.notify_all()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "stage": self.stage,
            "message": self.message,
            "progress": dict(self.progress),
            "counts": dict(self.counts),
            "spotify": dict(self.spotify) if self.spotify else None,
            "target": dict(self.target) if self.target else None,
            "error": self.error,
            "preview": self.preview,
            "options": dict(self.options),
        }

    # -- events --------------------------------------------------------
    def events_after(self, event_id: int) -> list[dict[str, Any]]:
        with self._lock:
            return [event for event in self._events if event["id"] > event_id]

    def wait_for_events(self, event_id: int, timeout: float) -> bool:
        with self._condition:
            if any(event["id"] > event_id for event in self._events):
                return True
            return self._condition.wait(timeout)

    @property
    def stream_finished(self) -> bool:
        with self._lock:
            return self.status in STREAM_END_STATUSES

    # -- control -------------------------------------------------------
    def cancel(self) -> None:
        with self._lock:
            if self.status in TERMINAL_STATUSES:
                return
            self.cancel_event.set()
            if self.status == "queued":
                self.status = "cancelled"
                self.stage = "cancelled"
                self.message = "Cancelled"
                self._emit_locked("state", self._snapshot_locked())
            elif self.stage not in {"done", "failed"}:
                self.message = "Cancelling…"
                self._emit_locked("state", self._snapshot_locked())

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise JobCancelled

    def begin_create(self) -> bool:
        with self._lock:
            if self._creating or self.status != "ready":
                return False
            if not self.headers:
                raise JobStateError(
                    "This preview was created without YouTube headers; start a "
                    "new transfer with headers to create the playlist."
                )
            self._creating = True
            return True

    def finish_create(self) -> None:
        with self._lock:
            self._creating = False

    def clear_credentials(self) -> None:
        with self._lock:
            self.headers = None

    def add_track(self, track: MatchedTrack) -> None:
        self.matches.append(track)
        self.emit("track", track_event(track))


class JobManager:
    """Owns live jobs and their worker threads."""

    def __init__(
        self,
        runner: Callable[[Job, TransferRequest], None] | None = None,
    ) -> None:
        self._runner = runner or run_job
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(self, request: TransferRequest) -> Job:
        job = Job(request)
        with self._lock:
            self._prune_locked()
            self._jobs[job.id] = job
        thread = threading.Thread(
            target=self._run,
            args=(job, request),
            name=f"spyt-job-{job.id}",
            daemon=True,
        )
        thread.start()
        return job

    def _run(self, job: Job, request: TransferRequest) -> None:
        try:
            self._runner(job, request)
        except Exception:
            logger.exception("unhandled worker error for job %s", job.id)
            job.update(
                status="failed",
                error="Unexpected internal error",
                message="Transfer failed",
            )
        finally:
            if job.status != "ready":
                job.clear_credentials()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def create_from_preview(
        self,
        job: Job,
        options: CreateOptions,
    ) -> None:
        if not job.begin_create():
            raise JobStateError("This job is not ready for playlist creation")

        matched = sum(1 for track in job.matches if track.status == "matched")
        job.update(
            status="running",
            stage="creating",
            message=f"Creating the playlist and adding 0 of {matched} tracks",
            progress={"done": 0, "total": matched},
        )

        thread = threading.Thread(
            target=self._create_worker,
            args=(job, options),
            name=f"spyt-create-{job.id}",
            daemon=True,
        )
        thread.start()

    def _create_worker(self, job: Job, options: CreateOptions) -> None:
        try:
            assert job.playlist is not None
            assert job.report is not None
            assert job.headers is not None

            request = TransferRequest(
                playlist=job.playlist.id,
                cookies="",
                headers="",
                name=options.name or job.options.get("name"),
                description=options.description,
                privacy=options.privacy or job.options.get("privacy") or "private",
                preview=False,
            )
            youtube = YouTubeMusicClient(
                headers=job.headers,
                verify_session=False,
            )
            perform_create(job, youtube, request)
        except JobCancelled:
            job.update(status="cancelled", message="Cancelled")
        except SpytError as exc:
            job.update(
                status="failed",
                error=str(exc),
                message="Could not create the playlist",
            )
        except Exception:
            logger.exception("playlist creation failed for job %s", job.id)
            job.update(
                status="failed",
                error="Unexpected internal error",
                message="Could not create the playlist",
            )
        finally:
            job.finish_create()
            job.clear_credentials()

    def _prune_locked(self) -> None:
        cutoff = time.time() - JOB_TTL_SECONDS
        stale = [
            job_id
            for job_id, job in self._jobs.items()
            if job.created_at < cutoff and job.status in TERMINAL_STATUSES
        ]
        for job_id in stale:
            del self._jobs[job_id]


def run_job(job: Job, request: TransferRequest) -> None:
    """Read Spotify, match every track, then create the YouTube playlist."""
    try:
        job.update(
            status="running",
            stage="spotify",
            message="Reading the Spotify playlist",
            progress={"done": 0, "total": None},
        )
        cookies = parse_cookies(request.cookies)

        with SpotifyWebClient(cookies=cookies) as spotify:
            playlist = spotify.fetch_playlist(
                request.playlist,
                progress=lambda done, total: _spotify_progress(job, done, total),
            )

        job.playlist = playlist
        job.update(
            spotify={
                "id": playlist.id,
                "name": playlist.name,
                "owner": playlist.owner.name if playlist.owner else None,
                "total_items": playlist.total_items,
                "url": f"https://open.spotify.com/playlist/{playlist.id}",
            },
            message=f'Reading "{playlist.name}"',
        )
        job.check_cancelled()

        headers = parse_headers(request.headers) if request.headers else None
        job.headers = headers

        job.update(stage="youtube", message="Connecting to YouTube Music")
        youtube = YouTubeMusicClient(headers=headers, verify_session=True)
        job.check_cancelled()

        tracks = playlist.tracks
        if request.max_items is not None:
            tracks = tracks[: request.max_items]
        total = len(tracks)
        job.total_tracks = total
        job.update(
            stage="matching",
            message=f"Matching 0 of {total} tracks",
            progress={"done": 0, "total": total},
        )

        first_position: dict[str, int] = {}

        def on_track(track: MatchedTrack) -> None:
            job.check_cancelled()
            mark_duplicate(track, first_position)
            job.add_track(track)
            done = len(job.matches)
            job.update(
                counts=_counts(job.matches, playlist),
                progress={"done": done, "total": total},
                message=f"Matching {done} of {total} tracks",
            )

        match_playlist(
            playlist,
            youtube.search_tracks,
            min_score=request.min_score,
            max_items=request.max_items,
            on_track=on_track,
        )
        job.check_cancelled()

        report = build_report(playlist, job.matches)
        job.report = report
        job.update(counts=_counts(job.matches, playlist))

        if request.preview:
            job.update(
                status="ready",
                stage="ready",
                message="Matches are ready to review",
            )
            return

        perform_create(job, youtube, request)
    except JobCancelled:
        job.update(status="cancelled", message="Cancelled")
    except SpytError as exc:
        job.update(
            status="failed",
            error=str(exc),
            message="Transfer failed",
        )
    except Exception:
        logger.exception("transfer failed for job %s", job.id)
        job.update(
            status="failed",
            error="Unexpected internal error",
            message="Transfer failed",
        )


def perform_create(job: Job, youtube: YouTubeMusicClient, request: TransferRequest) -> None:
    if job.report is None:
        raise SpytError("The match report is missing")

    matched = sum(1 for track in job.matches if track.status == "matched")
    if matched == 0:
        raise SpytError("No tracks matched, so there is nothing to add")

    title = request.name or (job.playlist.name if job.playlist else None)
    title = title or f"Spotify playlist {job.playlist.id if job.playlist else ''}".strip()
    description = request.description or (
        f"Imported from Spotify: https://open.spotify.com/playlist/"
        f"{job.playlist.id if job.playlist else ''}"
    )

    job.update(
        status="running",
        stage="creating",
        message=f"Creating the playlist and adding 0 of {matched} tracks",
        progress={"done": 0, "total": matched},
    )

    def on_progress(done: int, total: int) -> None:
        job.check_cancelled()
        job.update(
            target=job.report.target if job.report else None,
            counts=_counts(job.matches, job.playlist, added=done),
            progress={"done": done, "total": total},
            message=f"Adding tracks {done} of {total}",
        )

    added = create_target_playlist(
        youtube,
        job.report,
        title=title,
        description=description,
        privacy=request.privacy,
        progress=on_progress,
    )
    job.update(
        status="done",
        stage="done",
        target=job.report.target,
        counts=_counts(job.matches, job.playlist, added=added),
        message=f'Created "{title}" with {added} tracks',
    )


def track_event(track: MatchedTrack) -> dict[str, Any]:
    return {
        "position": track.position,
        "status": track.status,
        "spotify": track.spotify,
        "youtube": track.youtube,
        "score": track.score,
        "reason": track.reason,
        "duplicate_of": track.duplicate_of,
    }


def _counts(
    matches: list[MatchedTrack],
    playlist: Any,
    *,
    added: int = 0,
) -> dict[str, int]:
    counts = {
        "matched": 0,
        "unmatched": 0,
        "skipped": 0,
        "duplicate": 0,
        "added": added,
    }
    for track in matches:
        counts[track.status] = counts.get(track.status, 0) + 1
    # Items the Spotify reader itself skipped (episodes, unavailable entries)
    # still count towards the playlist so the summary is honest.
    counts["skipped"] += len(getattr(playlist, "skipped", []) or [])
    return counts


def _spotify_progress(job: Job, done: int, total: int) -> None:
    job.check_cancelled()
    job.update(
        progress={"done": done, "total": total},
        message=f"Reading {done} of {total} Spotify items",
    )
