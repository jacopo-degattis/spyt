"""YouTube Music browser-session authentication.

YouTube's web client no longer sends a single ``SAPISIDHASH``. It sends three
hashes (plain, first-party and third-party cookie variants) and includes the
account's user-session id (from ``DATASYNC_ID``) in the hash input:

    SAPISIDHASH <ts>_<sha1(user_session_id ts SAPISID origin)>_u
    SAPISID1PHASH ...
    SAPISID3PHASH ...

The user-session id is only available from the music.youtube.com page config,
so an authenticated client fetches it once before making API calls.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass

import requests

from ..errors import YouTubeAuthError
from .constants import USER_AGENT, WEB_PLAYER_HOME

_YT_CFG_RE = re.compile(r"ytcfg\.set\s*\(\s*(\{.+?\})\s*\)\s*;", re.DOTALL)
_COOKIE_RE_CACHE: dict[str, re.Pattern[str]] = {}


@dataclass
class BrowserSession:
    logged_in: bool = False
    user_session_id: str | None = None
    delegated_session_id: str | None = None
    session_index: int | None = None


def fetch_browser_session(
    headers: dict[str, str],
    *,
    timeout: float = 30.0,
    session: requests.Session | None = None,
) -> BrowserSession:
    """Read the signed-in account's data-sync id from the YouTube Music page."""
    cookie = headers.get("cookie")
    if not cookie:
        return BrowserSession()

    request_headers = {
        key: value
        for key, value in headers.items()
        if key
        not in {
            "authorization",
            "content-type",
            "content-length",
            "accept",
            "accept-encoding",
        }
    }
    request_headers.update(
        {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "cookie": cookie,
        }
    )
    request_headers.setdefault("user-agent", USER_AGENT)

    client = session or requests.Session()
    try:
        response = client.get(WEB_PLAYER_HOME, headers=request_headers, timeout=timeout)
    except requests.RequestException as exc:
        raise YouTubeAuthError(f"Could not reach YouTube Music: {exc}") from exc

    if response.status_code in {401, 403}:
        raise YouTubeAuthError(
            "YouTube Music rejected the session cookies. Copy fresh headers from "
            "a browser that is logged in to music.youtube.com."
        )
    if response.status_code >= 400:
        raise YouTubeAuthError(
            f"YouTube Music returned HTTP {response.status_code} while checking "
            "the session"
        )

    return parse_browser_session(response.text)


def parse_browser_session(html: str) -> BrowserSession:
    """Extract account/session information from the page's ``ytcfg`` blocks."""
    configs: list[dict] = []
    for block in _YT_CFG_RE.findall(html):
        try:
            config = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(config, dict):
            configs.append(config)

    chosen = next(
        (config for config in configs if config.get("LOGGED_IN") is True),
        configs[-1] if configs else None,
    )
    if chosen is None:
        return BrowserSession()

    logged_in = chosen.get("LOGGED_IN") is True
    if not logged_in:
        return BrowserSession(logged_in=False)

    data_sync_id = chosen.get("DATASYNC_ID")
    delegated, user = _parse_data_sync_id(data_sync_id)

    if isinstance(chosen.get("USER_SESSION_ID"), str):
        user = chosen["USER_SESSION_ID"] or None
    if isinstance(chosen.get("DELEGATED_SESSION_ID"), str):
        delegated = chosen["DELEGATED_SESSION_ID"] or None

    session_index = chosen.get("SESSION_INDEX")
    if not isinstance(session_index, int):
        session_index = None

    return BrowserSession(
        logged_in=True,
        user_session_id=user,
        delegated_session_id=delegated,
        session_index=session_index,
    )


def build_authorization(
    cookie_header: str,
    origin: str,
    *,
    user_session_id: str | None = None,
    now: float | None = None,
) -> str | None:
    """Build the three-value Google authorization header used by YouTube."""
    origin = origin.rstrip("/")
    timestamp = str(int(time.time() if now is None else now))
    user_session_id = user_session_id or None

    schemes = (
        ("SAPISIDHASH", "SAPISID", "__Secure-3PAPISID"),
        ("SAPISID1PHASH", "__Secure-1PAPISID", None),
        ("SAPISID3PHASH", "__Secure-3PAPISID", None),
    )

    values: list[str] = []
    for scheme, cookie_name, fallback_name in schemes:
        sid = _cookie_value(cookie_header, cookie_name)
        if not sid and fallback_name:
            sid = _cookie_value(cookie_header, fallback_name)
        if not sid:
            continue

        if user_session_id:
            hash_input = f"{user_session_id} {timestamp} {sid} {origin}"
        else:
            hash_input = f"{timestamp} {sid} {origin}"
        digest = hashlib.sha1(hash_input.encode()).hexdigest()
        token = f"{timestamp}_{digest}"
        if user_session_id:
            token += "_u"
        values.append(f"{scheme} {token}")

    return " ".join(values) or None


def _parse_data_sync_id(data_sync_id: object) -> tuple[str | None, str | None]:
    if not isinstance(data_sync_id, str) or not data_sync_id:
        return None, None
    first, _, second = data_sync_id.partition("||")
    if second:
        return first or None, second or None
    return None, first or None


def _cookie_value(cookie_header: str, name: str) -> str | None:
    pattern = _COOKIE_RE_CACHE.get(name)
    if pattern is None:
        pattern = re.compile(rf"(?:^|;\s*){re.escape(name)}=([^;]*)")
        _COOKIE_RE_CACHE[name] = pattern
    match = pattern.search(cookie_header)
    if not match:
        return None
    value = match.group(1).strip().strip('"')
    return value or None
