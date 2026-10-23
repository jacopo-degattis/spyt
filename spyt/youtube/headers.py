"""Parse the authenticated request headers copied from music.youtube.com.

Supported paste formats
-----------------------
* DevTools "Copy request headers" (``name: value`` lines, or Chrome's
  two-line-per-header variant)
* DevTools "Copy as cURL" (bash/zsh, single or multi-line)
* a JSON object mapping header names to values (``{"headers": {...}}`` is
  accepted too)
* a raw HTTP request line followed by headers

The parser keeps the user's cookies and regenerates the short-lived
``SAPISIDHASH`` authorization from ``__Secure-3PAPISID`` so a headers file stays
usable for as long as the browser session is valid.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import sys
import time
from pathlib import Path

from ..errors import HeaderError
from .constants import USER_AGENT

DEFAULT_ORIGIN = "https://music.youtube.com"

_SAPISID_RE = re.compile(r"(?:^|;\s*)__Secure-3PAPISID=([^;]+)")
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_REQUEST_LINE_RE = re.compile(r"^(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+\S+")

_IGNORED_HEADERS = {
    "host",
    "content-length",
    "content-encoding",
    "accept-encoding",
    "connection",
    "proxy-connection",
    "keep-alive",
    "transfer-encoding",
    "upgrade",
    "priority",
    "te",
    "pragma",
    "cache-control",
    "dnt",
    # Response-only headers that can appear when the whole DevTools panel is copied.
    "alt-used",
    "date",
    "server",
    "vary",
    "x-content-type-options",
}


def load_headers(source: str | Path) -> dict[str, str]:
    """Load a headers file, a raw headers string, or ``-`` for stdin."""
    return parse_headers(_read_source(source))


def parse_headers(text: str, *, now: float | None = None) -> dict[str, str]:
    """Parse and normalise a copied request-header block."""
    if not isinstance(text, str):
        raise HeaderError("Headers source must be text")

    text = text.strip().lstrip("\ufeff")
    if not text:
        raise HeaderError("The headers file is empty")

    if text.startswith(("{", "[")):
        raw = _parse_json(text)
    elif _looks_like_curl(text):
        raw = _parse_curl(text)
    else:
        raw = _parse_header_lines(text)

    if not raw:
        raise HeaderError(
            "Could not find any HTTP headers. Paste the request headers or a "
            "cURL command copied from music.youtube.com."
        )

    return _normalise(raw, now=now)


def sapisid_hash(sapisid: str, origin: str, *, now: float | None = None) -> str:
    """Build a ``SAPISIDHASH`` value the way the YouTube web client does."""
    timestamp = str(int(time.time() if now is None else now))
    digest = hashlib.sha1(f"{timestamp} {sapisid} {origin}".encode()).hexdigest()
    return f"SAPISIDHASH {timestamp}_{digest}"


def _read_source(source: str | Path) -> str:
    if isinstance(source, Path):
        try:
            return source.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise HeaderError(f"Could not read headers file {source}: {exc}") from exc

    if source == "-":
        return sys.stdin.read()

    candidate = Path(source)
    looks_like_path = "\n" not in source and len(source) < 4096
    if looks_like_path and candidate.is_file():
        try:
            return candidate.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise HeaderError(f"Could not read headers file {source}: {exc}") from exc

    return source


def _looks_like_curl(text: str) -> bool:
    return bool(re.search(r"(^|\s)(curl\b|--header\b|(?:^|\s)-H\s)", text))


def _parse_json(text: str) -> dict[str, str]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HeaderError(f"Invalid JSON in headers file: {exc}") from exc

    if isinstance(data, dict) and isinstance(data.get("headers"), (dict, list)):
        data = data["headers"]

    headers: dict[str, str] = {}
    if isinstance(data, dict):
        for name, value in data.items():
            if isinstance(name, str) and isinstance(value, str):
                _store(headers, name, value)
    elif isinstance(data, list):
        for entry in data:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            value = entry.get("value")
            if isinstance(name, str) and isinstance(value, str):
                _store(headers, name, value)
    else:
        raise HeaderError("JSON headers must be an object or a list of name/value objects")

    return headers


def _parse_curl(text: str) -> dict[str, str]:
    joined = re.sub(r"\\\r?\n", " ", text)
    joined = re.sub(r"\^\r?\n", " ", joined)
    headers: dict[str, str] = {}

    try:
        tokens = shlex.split(joined, posix=True)
    except ValueError:
        tokens = []

    if tokens:
        index = 0
        while index < len(tokens):
            token = tokens[index]

            if token in {"-H", "--header"} and index + 1 < len(tokens):
                _store_header_line(headers, tokens[index + 1])
                index += 2
                continue

            if token.startswith("--header="):
                _store_header_line(headers, token.split("=", 1)[1])
            elif token.startswith("-H") and len(token) > 2:
                _store_header_line(headers, token[2:])
            elif token in {"-b", "--cookie"} and index + 1 < len(tokens):
                _append(headers, "cookie", tokens[index + 1])
                index += 1
            elif token.startswith("--cookie="):
                _append(headers, "cookie", token.split("=", 1)[1])

            index += 1

    if not headers or "$'" in joined:
        # Fallback for quoting shlex cannot handle (for example $'...').
        fallback: dict[str, str] = {}
        pattern = re.compile(r"""(?:-H|--header)\s+\$?(['"])(.*?)\1""", re.DOTALL)
        for match in pattern.finditer(joined):
            _store_header_line(fallback, match.group(2))
        for key, value in fallback.items():
            headers.setdefault(key, value)

    return headers


def _parse_header_lines(text: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    pending_key: str | None = None
    last_key: str | None = None
    seen_first_line = False

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        stripped = line.strip()

        if not stripped:
            continue

        if not seen_first_line:
            seen_first_line = True
            if _REQUEST_LINE_RE.match(stripped):
                continue
            if stripped.startswith("HTTP/"):
                continue

        if pending_key is not None:
            _store(headers, pending_key, stripped)
            last_key = pending_key.lower()
            pending_key = None
            continue

        if line[:1] in {" ", "\t"} and last_key:
            headers[last_key] = f"{headers[last_key]} {stripped}"
            continue

        if stripped.startswith(":"):
            # HTTP/2 pseudo-header (:authority, :method, ...)
            continue

        if ":" in stripped:
            name, value = stripped.split(":", 1)
            name = name.strip()
            if _valid_name(name):
                if not value.strip() and stripped.endswith(":"):
                    pending_key = name
                else:
                    _store(headers, name, value.strip())
                    last_key = name.lower()
                continue

        if stripped.endswith(":"):
            name = stripped[:-1].strip()
            if _valid_name(name):
                pending_key = name
            continue

        if _valid_name(stripped):
            # Chrome sometimes copies the header name and its value on
            # separate lines.
            pending_key = stripped

    return headers


def _store_header_line(headers: dict[str, str], line: str) -> None:
    if ":" not in line:
        return
    name, value = line.split(":", 1)
    _store(headers, name, value)


def _store(headers: dict[str, str], name: str, value: str) -> None:
    name = name.strip()
    if not _valid_name(name):
        return
    key = name.lower()
    value = value.strip()
    if headers.get(key) and value:
        separator = "; " if key == "cookie" else ", "
        headers[key] = headers[key] + separator + value
    else:
        headers[key] = value


def _append(headers: dict[str, str], name: str, value: str) -> None:
    key = name.lower()
    value = value.strip()
    if headers.get(key):
        headers[key] = headers[key] + "; " + value
    else:
        headers[key] = value


def _normalise(raw: dict[str, str], *, now: float | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name, value in raw.items():
        key = name.strip().lower()
        if not _valid_name(key) or key.startswith("sec-") or key in _IGNORED_HEADERS:
            continue
        headers[key] = value.strip()

    headers.setdefault("user-agent", USER_AGENT)
    headers.setdefault("accept", "*/*")
    headers.setdefault("origin", DEFAULT_ORIGIN)
    headers["origin"] = headers["origin"].rstrip("/")
    headers.setdefault("referer", headers["origin"] + "/")
    headers["content-type"] = "application/json"

    cookie = headers.get("cookie")
    authorization = headers.get("authorization")

    if not cookie and not authorization:
        raise HeaderError(
            "No cookie or authorization header found. Copy the headers of an "
            "authenticated music.youtube.com request."
        )

    if cookie:
        sapisid = _extract_sapisid(cookie)
        if authorization:
            # Keep the browser's own Authorization value. Modern browsers send
            # three hashes (SAPISIDHASH/1PHASH/3PHASH) that include the
            # account's data-sync id; regenerating it here would break them.
            headers.setdefault("x-goog-authuser", "0")
        elif sapisid:
            headers["authorization"] = sapisid_hash(sapisid, headers["origin"], now=now)
            headers.setdefault("x-goog-authuser", "0")
        else:
            raise HeaderError(
                "The cookie header is missing __Secure-3PAPISID, so a "
                "SAPISIDHASH authorization cannot be generated. Make sure you "
                "copied the request while logged in."
            )
    elif authorization and authorization.upper().startswith("SAPISIDHASH"):
        raise HeaderError(
            "A SAPISIDHASH authorization needs the matching cookie header. "
            "Copy all request headers, not only the authorization line."
        )

    return headers


def _extract_sapisid(cookie: str) -> str | None:
    match = _SAPISID_RE.search(cookie)
    if not match:
        return None
    value = match.group(1).strip().strip('"')
    return value or None


def _valid_name(name: str) -> bool:
    return bool(name) and bool(_HEADER_NAME_RE.match(name))
