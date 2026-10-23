"""HTTP client for Spotify's internal pathfinder API."""

from __future__ import annotations

import json
import logging
import random
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import requests

from ..errors import (
    AuthError,
    PersistedQueryNotFoundError,
    PlaylistNotFoundError,
    SpotifyAPIError,
)
from .auth import SpotifyAuth
from .constants import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_TIMEOUT,
    FETCH_PLAYLIST_HASH,
    MAX_PAGE_SIZE,
    PATHFINDER_URL,
    USER_AGENT,
    WEB_PLAYER_APP_VERSION,
    WEB_PLAYER_HOME,
)
from .ids import parse_playlist_id
from .models import Playlist, SkippedItem, Track
from .playlist import parse_playlist_page

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]

_JS_BUNDLE_RE = re.compile(
    r"(?:https://open\.spotifycdn\.com)?/cdn/build/web-player/web-player\.[0-9a-f]+\.js"
)
_FETCH_PLAYLIST_HASH_RE = re.compile(
    r'"fetchPlaylist"\s*,\s*"query"\s*,\s*"([0-9a-f]{64})"'
)


class SpotifyWebClient:
    """Fetch playlists through the same API the Spotify web player uses.

    Example
    -------
    >>> cookies = load_cookies("cookies.txt")
    >>> client = SpotifyWebClient(cookies=cookies)
    >>> playlist = client.fetch_playlist("https://open.spotify.com/playlist/6yNRwWCVVNLEsif1esJ1Cc")
    >>> len(playlist.tracks)
    1021
    """

    def __init__(
        self,
        cookies: dict[str, str] | None = None,
        access_token: str | None = None,
        *,
        session: requests.Session | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = 4,
        user_agent: str = USER_AGENT,
        app_version: str = WEB_PLAYER_APP_VERSION,
        query_hash: str = FETCH_PLAYLIST_HASH,
    ) -> None:
        if not cookies and not access_token:
            raise AuthError(
                "Spotify needs either exported cookies (with an sp_dc cookie) "
                "or an access token"
            )

        self._owns_session = session is None
        self._session = session or requests.Session()
        self._timeout = timeout
        self._max_retries = max_retries
        self._user_agent = user_agent
        self._app_version = app_version
        self._query_hash = query_hash
        self._auth = SpotifyAuth(
            cookies=cookies,
            access_token=access_token,
            session=self._session,
            timeout=timeout,
        )

    def fetch_playlist(
        self,
        reference: str,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
        include_episodes: bool = False,
        max_items: int | None = None,
        progress: ProgressCallback | None = None,
    ) -> Playlist:
        """Fetch every item of a playlist, following pagination.

        Parameters
        ----------
        reference:
            A Spotify playlist URL, URI or bare id.
        page_size:
            Items requested per pathfinder call. Spotify accepted values up to
            5000 during testing; 250 is a good default.
        include_episodes:
            When false (default), podcast episodes are recorded in
            ``playlist.skipped`` instead of ``playlist.tracks``.
        max_items:
            Optional cap on how many playlist positions to read.
        progress:
            Called with ``(items_processed, total_items)`` after every page.
        """
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
        if max_items is not None and max_items < 1:
            raise ValueError("max_items must be positive")

        playlist_id = parse_playlist_id(reference)
        offset = 0
        header: Playlist | None = None
        tracks: list[Track] = []
        skipped: list[SkippedItem] = []

        while True:
            limit = page_size
            if header is not None:
                remaining_total = header.total_items - offset
                if remaining_total > 0:
                    limit = min(limit, remaining_total)
            if max_items is not None:
                limit = min(limit, max_items - offset)
            if limit <= 0:
                break

            payload = self._fetch_page(playlist_id, offset=offset, limit=limit)
            page = parse_playlist_page(payload, playlist_id=playlist_id, offset=offset)

            if header is None:
                header = page.playlist
            else:
                header.total_items = page.total_items

            for item in page.items:
                if isinstance(item, Track):
                    if item.kind == "episode" and not include_episodes:
                        skipped.append(
                            SkippedItem(
                                position=item.position,
                                reason="episode",
                                uri=item.uri,
                                name=item.name,
                            )
                        )
                    else:
                        tracks.append(item)
                else:
                    skipped.append(item)

            offset += len(page.items)
            if progress is not None:
                progress(offset, page.total_items)

            if not page.items or offset >= page.total_items:
                break

        if header is None:
            # No page was requested at all (max_items < 1 or empty playlist).
            header = Playlist(
                id=playlist_id,
                uri=f"spotify:playlist:{playlist_id}",
                name="",
            )

        header.total_items = max(header.total_items, len(tracks) + len(skipped))
        header.tracks = tracks
        header.skipped = skipped
        header.fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return header

    def close(self) -> None:
        if self._owns_session:
            self._session.close()

    def __enter__(self) -> SpotifyWebClient:  # noqa: PYI034 (typing.Self needs 3.11)
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _fetch_page(self, playlist_id: str, *, offset: int, limit: int) -> dict[str, Any]:
        data = {
            "variables": {
                "uri": f"spotify:playlist:{playlist_id}",
                "offset": offset,
                "limit": limit,
                "enableWatchFeedEntrypoint": True,
                "includeEpisodeContentRatingsV2": True,
            },
            "operationName": "fetchPlaylist",
            "extensions": {
                "persistedQuery": {"version": 1, "sha256Hash": self._query_hash}
            },
        }

        attempts = 0
        refreshed_auth = False
        refreshed_hash = False

        while attempts < max(self._max_retries + 2, 6):
            attempts += 1
            access_token = self._auth.get_access_token()
            headers = self._request_headers(access_token)
            client_token = self._auth.get_client_token()
            if client_token:
                headers["client-token"] = client_token

            try:
                response = self._session.post(
                    PATHFINDER_URL,
                    headers=headers,
                    json=data,
                    timeout=self._timeout,
                )
            except requests.RequestException as exc:
                if attempts <= self._max_retries:
                    self._backoff(attempts)
                    continue
                raise SpotifyAPIError(f"Could not reach Spotify: {exc}") from exc

            if response.status_code == 401:
                if refreshed_auth:
                    raise AuthError(
                        "Spotify rejected the access token twice. Export fresh "
                        "cookies from open.spotify.com."
                    )
                self._auth.refresh_access_token()
                refreshed_auth = True
                continue

            if response.status_code == 429:
                delay = _retry_after(response, default=self._backoff_delay(attempts))
                logger.warning("Spotify rate limit hit, waiting %.1fs", delay)
                time.sleep(delay)
                continue

            if response.status_code >= 500:
                if attempts <= self._max_retries:
                    self._backoff(attempts)
                    continue
                raise SpotifyAPIError(
                    f"Spotify is unavailable (HTTP {response.status_code})",
                    status_code=response.status_code,
                )

            payload = _decode_json(response)
            message = _error_message(payload) or response.text[:300]
            errors = payload.get("errors")

            if errors or response.status_code >= 400:
                if _is_persisted_query_error(message, payload):
                    if not refreshed_hash:
                        new_hash = self._discover_query_hash()
                        if new_hash and new_hash != self._query_hash:
                            logger.info("Refreshed fetchPlaylist query hash")
                            self._query_hash = new_hash
                            data["extensions"]["persistedQuery"]["sha256Hash"] = new_hash
                            refreshed_hash = True
                            continue
                    raise PersistedQueryNotFoundError(
                        "Spotify changed the fetchPlaylist query. Spyt could not "
                        "discover the new hash automatically; update "
                        "SPYT_FETCH_PLAYLIST_HASH.",
                        payload=payload,
                    )

                if _is_auth_error(message):
                    if refreshed_auth:
                        raise AuthError(
                            "Spotify rejected the access token twice. Export fresh "
                            "cookies from open.spotify.com."
                        )
                    self._auth.refresh_access_token()
                    refreshed_auth = True
                    continue

                raise SpotifyAPIError(
                    message,
                    status_code=response.status_code if response.status_code >= 400 else None,
                    payload=payload,
                )

            playlist = (payload.get("data") or {}).get("playlistV2")
            if isinstance(playlist, dict) and playlist.get("__typename") == "NotFound":
                raise PlaylistNotFoundError(playlist_id, playlist.get("message"))

            return payload

        raise SpotifyAPIError("Spotify did not answer after several attempts")

    def _request_headers(self, access_token: str) -> dict[str, str]:
        return {
            "User-Agent": self._user_agent,
            "Accept": "application/json",
            "Content-Type": "application/json;charset=UTF-8",
            "Referer": WEB_PLAYER_HOME,
            "Origin": WEB_PLAYER_HOME.rstrip("/"),
            "app-platform": "WebPlayer",
            "spotify-app-version": self._app_version,
            "Authorization": f"Bearer {access_token}",
        }

    def _discover_query_hash(self) -> str | None:
        """Read the current persisted-query hash from Spotify's JS bundle."""
        try:
            home = self._session.get(
                WEB_PLAYER_HOME,
                headers={
                    "User-Agent": self._user_agent,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en",
                },
                timeout=self._timeout,
            )
            home.raise_for_status()
            match = _JS_BUNDLE_RE.search(home.text)
            if not match:
                return None

            bundle_url = match.group(0)
            if bundle_url.startswith("/"):
                bundle_url = "https://open.spotifycdn.com" + bundle_url

            bundle = self._session.get(
                bundle_url,
                headers={
                    "User-Agent": self._user_agent,
                    "Accept": "*/*",
                    "Accept-Encoding": "gzip, deflate",
                },
                timeout=self._timeout,
            )
            bundle.raise_for_status()
            hash_match = _FETCH_PLAYLIST_HASH_RE.search(bundle.text)
            return hash_match.group(1) if hash_match else None
        except requests.RequestException as exc:
            logger.debug("Could not discover fetchPlaylist hash: %s", exc)
            return None

    def _backoff(self, attempt: int) -> None:
        time.sleep(self._backoff_delay(attempt))

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        return min(2 ** (attempt - 1), 30) + random.uniform(0, 0.5)


def _decode_json(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise SpotifyAPIError(
            f"Spotify returned invalid JSON (HTTP {response.status_code}): "
            f"{response.text[:300]}",
            status_code=response.status_code,
        ) from exc
    if not isinstance(payload, dict):
        raise SpotifyAPIError(
            "Unexpected JSON payload from Spotify", status_code=response.status_code
        )
    return payload


def _error_message(payload: dict[str, Any]) -> str | None:
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            message = first.get("message")
            if message:
                return str(message)
        return str(first)
    return None


def _is_persisted_query_error(message: str, payload: dict[str, Any]) -> bool:
    lowered = message.lower().replace(" ", "")
    if "persistedquery" in lowered and ("notfound" in lowered or "not found" in message.lower()):
        return True
    if "sha" in lowered and "does not match" in message.lower():
        return True
    return "persistedquerynotfound" in json.dumps(payload).lower().replace(" ", "")


def _is_auth_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        phrase in lowered
        for phrase in (
            "unauthenticated",
            "invalid access token",
            "access token is invalid",
            "token expired",
            "token is expired",
            "invalid token",
        )
    )


def _retry_after(response: requests.Response, *, default: float) -> float:
    value = response.headers.get("Retry-After")
    if value and value.strip().isdigit():
        return min(float(value.strip()), 60.0)
    return min(default, 30.0)
