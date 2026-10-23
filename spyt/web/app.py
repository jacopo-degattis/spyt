"""FastAPI application serving the local Spyt web UI."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..errors import SpytError
from ..match import DEFAULT_MIN_SCORE
from ..spotify import parse_cookies, parse_playlist_id
from ..youtube import parse_headers
from .jobs import (
    CreateOptions,
    JobManager,
    JobStateError,
    TransferRequest,
)

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_FILE = STATIC_DIR / "index.html"


class TransferPayload(BaseModel):
    playlist: str = Field(min_length=1, max_length=2048)
    cookies: str = Field(min_length=1, max_length=1_000_000)
    headers: str | None = Field(default=None, max_length=2_000_000)
    name: str | None = Field(default=None, max_length=150)
    description: str | None = Field(default=None, max_length=5000)
    privacy: Literal["private", "unlisted", "public"] = "private"
    max_items: int | None = Field(default=None, ge=1, le=100_000)
    min_score: float = Field(default=DEFAULT_MIN_SCORE, ge=0.0, le=1.0)
    preview: bool = False


class CreatePayload(BaseModel):
    name: str | None = Field(default=None, max_length=150)
    description: str | None = Field(default=None, max_length=5000)
    privacy: Literal["private", "unlisted", "public"] | None = None


def create_app(manager: JobManager | None = None) -> FastAPI:
    jobs = manager or JobManager()
    app = FastAPI(
        title="Spyt",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.jobs = jobs
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index() -> FileResponse:
        return FileResponse(INDEX_FILE, media_type="text/html")

    @app.get("/favicon.svg")
    async def favicon() -> FileResponse:
        return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/jobs")
    async def start_job(payload: TransferPayload) -> dict[str, str]:
        _validate(payload)
        request = TransferRequest(
            playlist=payload.playlist.strip(),
            cookies=payload.cookies,
            headers=payload.headers,
            name=_clean(payload.name),
            description=_clean(payload.description),
            privacy=payload.privacy,
            max_items=payload.max_items,
            min_score=payload.min_score,
            preview=payload.preview,
        )
        job = jobs.start(request)
        return {"id": job.id}

    @app.get("/api/jobs/{job_id}")
    async def job_state(job_id: str) -> dict:
        return _get_job(jobs, job_id).snapshot()

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, after: int = 0) -> StreamingResponse:
        job = _get_job(jobs, job_id)

        async def stream():
            cursor = max(0, after)
            while True:
                for event in job.events_after(cursor):
                    cursor = event["id"]
                    data = json.dumps(event["data"], ensure_ascii=False)
                    yield (
                        f"id: {cursor}\n"
                        f"event: {event['type']}\n"
                        f"data: {data}\n\n"
                    )
                if job.stream_finished and not job.events_after(cursor):
                    break
                woke = await asyncio.to_thread(job.wait_for_events, cursor, 15.0)
                if not woke:
                    yield ": keep-alive\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str) -> dict:
        job = _get_job(jobs, job_id)
        job.cancel()
        return job.snapshot()

    @app.post("/api/jobs/{job_id}/create")
    async def create_playlist(job_id: str, payload: CreatePayload) -> dict:
        job = _get_job(jobs, job_id)
        options = CreateOptions(
            name=_clean(payload.name),
            description=_clean(payload.description),
            privacy=payload.privacy,
        )
        try:
            jobs.create_from_preview(job, options)
        except JobStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return job.snapshot()

    @app.get("/api/jobs/{job_id}/report")
    async def download_report(job_id: str) -> Response:
        job = _get_job(jobs, job_id)
        if job.report is None:
            raise HTTPException(status_code=404, detail="The report is not ready yet")
        payload = json.dumps(
            job.report.to_dict(),
            ensure_ascii=False,
            indent=2,
        )
        playlist_id = (job.playlist.id if job.playlist else None) or "playlist"
        filename = f"spyt-{playlist_id}-report.json"
        return Response(
            content=payload + "\n",
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    return app


def run(
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    open_browser: bool = True,
    log_level: str = "warning",
) -> None:
    """Run the web UI with uvicorn."""
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "The web UI needs FastAPI and uvicorn. Install them with:\n"
            "  pip install -e '.[web]'"
        ) from exc

    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{display_host}:{port}/"
    print(f"Spyt web UI: {url}", file=sys.stderr)
    print("Press Ctrl+C to stop.", file=sys.stderr)

    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    uvicorn.run(
        create_app(),
        host=host,
        port=port,
        log_level=log_level,
    )


def _validate(payload: TransferPayload) -> None:
    try:
        parse_playlist_id(payload.playlist)
    except SpytError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid playlist: {exc}") from exc

    try:
        parse_cookies(payload.cookies)
    except SpytError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Spotify cookies: {exc}",
        ) from exc

    if payload.headers:
        try:
            headers = parse_headers(payload.headers)
        except SpytError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"YouTube Music headers: {exc}",
            ) from exc
        if "cookie" not in headers:
            raise HTTPException(
                status_code=400,
                detail=(
                    "YouTube Music headers have no Cookie header. Copy the full "
                    "request headers while logged in."
                ),
            )
    elif not payload.preview:
        raise HTTPException(
            status_code=400,
            detail=(
                "YouTube Music headers are required to create a playlist. "
                "Enable preview mode to match tracks without them."
            ),
        )


def _get_job(jobs: JobManager, job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


app = create_app()
