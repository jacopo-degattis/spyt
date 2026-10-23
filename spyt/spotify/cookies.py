"""Read cookies exported from a browser.

Supported formats
-----------------
* Netscape ``cookies.txt`` (the format used by "Get cookies.txt LOCALLY")
* JSON exported by Cookie-Editor / EditThisCookie (a list of cookie objects)
* Playwright/Puppeteer storage state (``{"cookies": [...]}``)
* A plain ``name=value; name=value`` Cookie header
* A bare ``sp_dc`` value copied from devtools

Only cookies for ``spotify.com`` are kept.
"""

from __future__ import annotations

import json
import re
import string
import sys
from pathlib import Path
from typing import Any

from ..errors import CookieError

_COOKIE_NAME_CHARS = set(string.ascii_letters + string.digits + "_-")


def load_cookies(source: str | Path) -> dict[str, str]:
    """Load Spotify cookies from a file path, raw text, or ``-`` for stdin."""
    return parse_cookies(_read_source(source))


def parse_cookies(text: str) -> dict[str, str]:
    """Parse cookies from any of the supported text formats."""
    if not isinstance(text, str):
        raise CookieError("Cookie source must be text")

    text = text.strip().lstrip("\ufeff")
    if not text:
        raise CookieError("The cookie source is empty")

    cookies: dict[str, str] = {}

    json_cookies = _try_json(text)
    if json_cookies is not None:
        cookies.update(json_cookies)

    if not cookies:
        cookies.update(_try_netscape(text))

    if not cookies:
        cookies.update(_try_cookie_header(text))

    if not cookies:
        raise CookieError(
            "Could not recognise the cookie format. Export cookies as Netscape "
            "cookies.txt or Cookie-Editor JSON."
        )

    return cookies


def _read_source(source: str | Path) -> str:
    if isinstance(source, Path):
        try:
            return source.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise CookieError(f"Could not read cookie file {source}: {exc}") from exc

    if source == "-":
        return sys.stdin.read()

    candidate = Path(source)
    looks_like_path = "\n" not in source and len(source) < 4096
    if looks_like_path and candidate.is_file():
        try:
            return candidate.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise CookieError(f"Could not read cookie file {source}: {exc}") from exc

    return source


def _try_json(text: str) -> dict[str, str] | None:
    if not text.startswith(("{", "[")):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    if isinstance(data, dict):
        raw_cookies = data.get("cookies")
        if isinstance(raw_cookies, list):
            return _from_cookie_objects(raw_cookies)
        if isinstance(raw_cookies, dict):
            return _from_mapping(raw_cookies)
        return _from_mapping(data)

    if isinstance(data, list):
        return _from_cookie_objects(data)

    return None


def _from_cookie_objects(entries: list[Any]) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        value = entry.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        if not _valid_name(name):
            continue
        domain = entry.get("domain")
        if not _spotify_domain(domain):
            continue
        cookies[name] = value
    return cookies


def _from_mapping(mapping: dict[str, Any]) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for name, value in mapping.items():
        if isinstance(name, str) and isinstance(value, str) and _valid_name(name):
            cookies[name] = value
    return cookies


def _try_netscape(text: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_") :]
        elif line.startswith("#"):
            continue

        parts = line.split("\t")
        if len(parts) < 7:
            continue

        domain, _flag, _path, _secure, _expires, name, value = parts[:7]
        if not _valid_name(name) or not _spotify_domain(domain):
            continue
        cookies[name] = value
    return cookies


def _try_cookie_header(text: str) -> dict[str, str]:
    stripped = text.strip()
    if not stripped:
        return {}

    # A bare cookie value (commonly the sp_dc value itself). Spotify's sp_dc
    # values are long opaque tokens; requiring a realistic length avoids
    # treating ordinary prose as a cookie.
    if (
        "=" not in stripped
        and "\n" not in stripped
        and len(stripped) >= 40
        and re.fullmatch(r"[A-Za-z0-9._~+/=-]+", stripped)
    ):
        return {"sp_dc": stripped}

    if "\n" in stripped and ";" not in stripped:
        return {}

    cookies: dict[str, str] = {}
    for part in stripped.replace("\n", ";").split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if name and _valid_name(name):
            cookies[name] = value.strip()
    return cookies


def _valid_name(name: str) -> bool:
    return bool(name) and all(char in _COOKIE_NAME_CHARS for char in name)


def _spotify_domain(domain: Any) -> bool:
    if not isinstance(domain, str) or not domain:
        return True
    normalised = domain.lstrip(".").lower()
    return normalised == "spotify.com" or normalised.endswith(".spotify.com")
