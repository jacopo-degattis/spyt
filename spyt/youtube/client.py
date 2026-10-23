"""YouTube Music client built on top of ytmusicapi."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from typing import TypeVar

from ytmusicapi import YTMusic
from ytmusicapi.exceptions import YTMusicError, YTMusicServerError, YTMusicUserError

from ..errors import (
    YouTubeAPIError,
    YouTubeAuthError,
    YouTubePlaylistNotFoundError,
)
from .auth import build_authorization, fetch_browser_session
from .constants import DEFAULT_TIMEOUT
from .ids import parse_playlist_id
from .mapping import map_playlist, map_search_result
from .models import Playlist, SearchTrack

logger = logging.getLogger(__name__)

_T = TypeVar("_T")
_TRANSIENT_MARKERS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "timeout",
    "timed out",
    "connection",
    "temporarily",
)
WRITE_BATCH_SIZE = 50


class _BrowserYTMusic(YTMusic):
    """YTMusic that keeps the browser's triple-hash authorization fresh."""

    def __init__(self, *, auth: dict[str, str], authorization_builder, **kwargs) -> None:
        self._authorization_builder = authorization_builder
        super().__init__(auth=auth, **kwargs)

    @property
    def headers(self):
        headers = self.base_headers
        authorization = self._authorization_builder()
        if authorization:
            headers["authorization"] = authorization
        return headers


class YouTubeMusicClient:
    """Fetch playlists from YouTube Music.

    Passing ``headers`` (parsed from a copied authenticated request) enables
    access to private library playlists. With ``headers=None`` the client is
    anonymous and can only read public playlists.
    """

    def __init__(
        self,
        headers: dict[str, str] | None = None,
        *,
        language: str = "en",
        location: str = "",
        verify_session: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._language = language
        try:
            if headers and _is_browser_auth(headers):
                session = fetch_browser_session(headers, timeout=timeout)
                if verify_session and not session.logged_in:
                    raise YouTubeAuthError(
                        "YouTube Music reports the supplied headers as logged "
                        "out (LOGGED_IN=false). Log in to music.youtube.com, "
                        "open a playlist, and copy fresh request headers."
                    )

                prepared = dict(headers)
                if session.session_index is not None:
                    prepared["x-goog-authuser"] = str(session.session_index)
                if session.delegated_session_id:
                    prepared["x-goog-pageid"] = session.delegated_session_id

                origin = prepared.get("origin", "https://music.youtube.com").rstrip("/")
                prepared["x-origin"] = origin
                cookie = prepared.get("cookie", "")

                if session.logged_in:
                    user_session_id = session.user_session_id

                    def authorization_builder() -> str | None:
                        return build_authorization(
                            cookie,
                            origin,
                            user_session_id=user_session_id,
                        )

                    generated = authorization_builder()
                    if generated:
                        prepared["authorization"] = generated
                else:
                    # Verification was disabled: trust the pasted header.
                    def authorization_builder() -> str | None:
                        return None

                self._ytm = _BrowserYTMusic(
                    auth=prepared,
                    authorization_builder=authorization_builder,
                    language=language,
                    location=location,
                )
            elif headers:
                self._ytm = YTMusic(
                    auth=headers,
                    language=language,
                    location=location,
                )
            else:
                self._ytm = YTMusic(language=language, location=location)
        except YTMusicError as exc:
            raise YouTubeAuthError(str(exc)) from exc

    def fetch_playlist(
        self,
        reference: str,
        *,
        include_episodes: bool = False,
        max_items: int | None = None,
    ) -> Playlist:
        """Fetch a playlist and normalise all of its items.

        YouTube Music returns 100 items per page and controls the page size, so
        there is no page-size option. ``max_items`` caps how many playlist
        positions are read.
        """
        if max_items is not None and max_items < 1:
            raise ValueError("max_items must be positive")

        playlist_id = parse_playlist_id(reference)
        fetch_limit: int | None
        if max_items is None:
            fetch_limit = None
        elif max_items <= 100:
            # The first page always arrives; limit=0 disables continuations.
            fetch_limit = 0
        else:
            # ytmusicapi counts only continuation items against this limit.
            fetch_limit = max(1, max_items - 100)

        try:
            raw = self._ytm.get_playlist(playlist_id, limit=fetch_limit)
        except YTMusicServerError as exc:
            raise _map_server_error(playlist_id, exc) from exc
        except YTMusicUserError as exc:
            raise YouTubeAPIError(str(exc)) from exc
        except KeyError as exc:
            raise YouTubePlaylistNotFoundError(
                playlist_id,
                "YouTube Music returned no playlist page. The playlist may be "
                "private, deleted, empty, or the copied headers may be stale.",
            ) from exc
        except YTMusicError as exc:
            raise YouTubeAPIError(str(exc)) from exc

        if not isinstance(raw, dict):
            raise YouTubeAPIError("Unexpected response from YouTube Music")

        return map_playlist(
            raw,
            playlist_id=playlist_id,
            include_episodes=include_episodes,
            max_items=max_items,
        )

    def search_tracks(
        self,
        query: str,
        *,
        filter: str = "songs",
        limit: int = 8,
    ) -> list[SearchTrack]:
        """Search the YouTube Music catalogue and return normalised results."""
        if not query.strip():
            raise ValueError("query must not be empty")
        if limit < 1:
            raise ValueError("limit must be positive")

        try:
            raw = _retry(lambda: self._ytm.search(query, filter=filter, limit=limit))
        except YTMusicServerError as exc:
            raise _map_api_error(exc) from exc
        except YTMusicUserError as exc:
            raise YouTubeAPIError(str(exc)) from exc
        except YTMusicError as exc:
            raise YouTubeAPIError(str(exc)) from exc

        if not isinstance(raw, list):
            raise YouTubeAPIError("Unexpected response from YouTube Music search")

        results: list[SearchTrack] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            mapped = map_search_result(item)
            if mapped is not None:
                results.append(mapped)
        return results

    def create_playlist(
        self,
        title: str,
        *,
        description: str = "",
        privacy: str = "PRIVATE",
    ) -> str:
        """Create an empty playlist and return its id."""
        title = title.strip()
        if not title:
            raise ValueError("playlist title must not be empty")
        privacy = privacy.upper()
        if privacy not in {"PRIVATE", "UNLISTED", "PUBLIC"}:
            raise ValueError(f"invalid privacy: {privacy!r}")

        try:
            result = _retry(
                lambda: self._ytm.create_playlist(
                    title,
                    description,
                    privacy_status=privacy,
                )
            )
        except YTMusicServerError as exc:
            raise _map_api_error(exc) from exc
        except YTMusicUserError as exc:
            raise YouTubeAPIError(str(exc)) from exc
        except YTMusicError as exc:
            raise YouTubeAPIError(str(exc)) from exc

        if isinstance(result, str) and result:
            return result
        raise YouTubeAPIError(
            f"YouTube Music did not create the playlist: {result!r}"
        )

    def add_playlist_items(
        self,
        playlist_id: str,
        video_ids: Iterable[str],
        *,
        batch_size: int = WRITE_BATCH_SIZE,
        progress: Callable[[int, int], None] | None = None,
    ) -> int:
        """Add video ids to a playlist in small batches; return the number added."""
        ids = [video_id for video_id in video_ids if video_id]
        if not ids:
            return 0
        if batch_size < 1:
            raise ValueError("batch_size must be positive")

        total = len(ids)
        added = 0
        for start in range(0, total, batch_size):
            batch = ids[start : start + batch_size]
            try:
                result = _retry(
                    lambda batch=batch: self._ytm.add_playlist_items(
                        playlist_id,
                        batch,
                    )
                )
            except YTMusicServerError as exc:
                raise _map_api_error(exc) from exc
            except YTMusicUserError as exc:
                raise YouTubeAPIError(str(exc)) from exc
            except YTMusicError as exc:
                raise YouTubeAPIError(str(exc)) from exc

            if not _write_succeeded(result):
                raise YouTubeAPIError(
                    "YouTube Music rejected the playlist update: "
                    f"{result!r} (added {added} of {total})"
                )
            added += len(batch)
            if progress is not None:
                progress(added, total)
        return added


def _retry(call: Callable[[], _T], *, attempts: int = 3, base_delay: float = 0.5) -> _T:
    """Retry YouTube Music calls that failed for transient reasons."""
    for attempt in range(attempts):
        try:
            return call()
        except YTMusicServerError as exc:
            message = str(exc).lower()
            transient = any(marker in message for marker in _TRANSIENT_MARKERS)
            if not transient or attempt == attempts - 1:
                raise
            delay = base_delay * (2**attempt)
            logger.debug("retrying YouTube Music call in %.1fs after: %s", delay, exc)
            time.sleep(delay)
    raise AssertionError("unreachable")


def _write_succeeded(result: object) -> bool:
    if isinstance(result, str):
        return "SUCCEEDED" in result.upper()
    if isinstance(result, dict):
        return "SUCCEEDED" in str(result.get("status", "")).upper()
    return False


def _map_api_error(exc: YTMusicServerError) -> YouTubeAPIError:
    message = str(exc)
    lowered = message.lower()
    if any(
        marker in lowered
        for marker in ("401", "403", "unauthorized", "forbidden", "not logged in")
    ):
        return YouTubeAuthError(
            "YouTube Music rejected the supplied headers. Copy a fresh "
            "authenticated request from music.youtube.com and try again."
        )
    return YouTubeAPIError(message)


def _map_server_error(playlist_id: str, exc: YTMusicServerError) -> YouTubeAPIError:
    message = str(exc)
    lowered = message.lower()
    if any(
        marker in lowered
        for marker in ("401", "403", "unauthorized", "forbidden", "not logged in")
    ):
        return YouTubeAuthError(
            "YouTube Music rejected the supplied headers. Copy a fresh "
            "authenticated request from music.youtube.com and try again."
        )
    if any(marker in lowered for marker in ("404", "not found", "does not exist")):
        return YouTubePlaylistNotFoundError(playlist_id, message)
    return YouTubeAPIError(message)


def _is_browser_auth(headers: dict[str, str]) -> bool:
    authorization = headers.get("authorization", "")
    return bool(headers.get("cookie")) and not authorization.lower().startswith("bearer")
