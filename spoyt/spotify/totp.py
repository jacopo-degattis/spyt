"""TOTP generation for Spotify's web-player token endpoint.

Spotify no longer exposes ``get_access_token``. The web player now derives a
one-time password from a per-build secret. ``secretDict.json`` (maintained by
the community project spot-secrets-go) maps app versions to the XOR-obfuscated
secret bytes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import time
from pathlib import Path
from typing import Any

import requests

from ..errors import AuthError
from .constants import SECRETS_URL, USER_AGENT

logger = logging.getLogger(__name__)


def load_secrets(
    session: requests.Session,
    url: str | None = None,
    *,
    timeout: float = 15.0,
) -> dict[str, list[int]]:
    """Download the secret dictionary, falling back to a local cache."""
    url = url or SECRETS_URL
    cache_path = _cache_path()

    try:
        response = session.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        secrets = _validate_secrets(data)
        _write_cache(cache_path, secrets)
        return secrets
    except (requests.RequestException, ValueError, AuthError) as exc:
        if cache_path.is_file():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                logger.warning("Could not refresh Spotify TOTP secrets, using cached copy")
                return _validate_secrets(cached)
            except (OSError, ValueError, AuthError):
                pass
        raise AuthError(
            "Could not download Spotify's TOTP secrets from "
            f"{url}. Check your network connection or set SPOYT_SPOTIFY_SECRETS_URL. "
            f"Underlying error: {exc}"
        ) from exc


def candidate_secrets(
    secrets: dict[str, list[int]], *, limit: int = 3
) -> list[tuple[str, list[int]]]:
    """Return the newest (version, cipher) pairs, newest first."""
    if not secrets:
        raise AuthError("Spotify's TOTP secret list is empty")

    def version_key(item: tuple[str, list[int]]) -> int:
        try:
            return int(item[0])
        except (TypeError, ValueError):
            return -1

    ordered = sorted(secrets.items(), key=version_key, reverse=True)
    candidates = [(str(version), cipher) for version, cipher in ordered if cipher]
    if not candidates:
        raise AuthError("No usable Spotify TOTP secrets found")
    return candidates[: max(1, limit)]


def latest_secret(secrets: dict[str, list[int]]) -> tuple[str, list[int]]:
    """Return the (version, cipher) pair with the highest version number."""
    return candidate_secrets(secrets, limit=1)[0]


def derive_secret(cipher: list[int]) -> str:
    """Turn the obfuscated cipher bytes into a base32 TOTP secret."""
    decoded = "".join(str(byte ^ ((index % 33) + 9)) for index, byte in enumerate(cipher))
    return base64.b32encode(decoded.encode("ascii")).decode("ascii").rstrip("=")


def generate_totp(secret: str, *, timestamp: float | None = None, digits: int = 6) -> str:
    """RFC 6238 TOTP (SHA-1, 30 second period)."""
    timestamp = time.time() if timestamp is None else timestamp
    padding = "=" * (-len(secret) % 8)
    try:
        key = base64.b32decode(secret.upper() + padding)
    except ValueError as exc:
        raise AuthError("Invalid Spotify TOTP secret") from exc

    counter = int(timestamp // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{truncated % (10**digits):0{digits}d}"


def generate_code(cipher: list[int], *, timestamp: float | None = None) -> str:
    return generate_totp(derive_secret(cipher), timestamp=timestamp)


def _validate_secrets(data: Any) -> dict[str, list[int]]:
    if not isinstance(data, dict) or not data:
        raise AuthError("Unexpected format for Spotify's TOTP secret list")

    secrets: dict[str, list[int]] = {}
    for version, cipher in data.items():
        if not isinstance(cipher, list) or not all(isinstance(byte, int) for byte in cipher):
            continue
        secrets[str(version)] = cipher

    if not secrets:
        raise AuthError("No usable Spotify TOTP secrets found")
    return secrets


def _cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    cache_root = Path(base) if base else Path.home() / ".cache"
    return cache_root / "spoyt" / "spotify_totp_secrets.json"


def _write_cache(path: Path, data: dict[str, list[int]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        logger.debug("Could not write TOTP secret cache to %s", path)
