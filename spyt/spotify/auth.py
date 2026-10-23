"""Authentication against Spotify's web player.

The web player no longer has a public token endpoint: it authenticates with a
TOTP derived from a per-build secret, combined with the long-lived ``sp_dc``
cookie. A manually supplied access token is also supported, mainly for
debugging since it expires after roughly an hour.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import requests

from ..errors import AuthError
from .constants import (
    CLIENT_TOKEN_URL,
    DEFAULT_TIMEOUT,
    TOKEN_URL,
    USER_AGENT,
    WEB_PLAYER_APP_VERSION,
    WEB_PLAYER_CLIENT_ID,
    WEB_PLAYER_HOME,
)
from .totp import candidate_secrets, generate_code, load_secrets

logger = logging.getLogger(__name__)


class SpotifyAuth:
    """Produces access tokens and client tokens for the pathfinder API."""

    def __init__(
        self,
        cookies: dict[str, str] | None = None,
        access_token: str | None = None,
        *,
        session: requests.Session | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        secrets_url: str | None = None,
    ) -> None:
        self._cookies = dict(cookies or {})
        self._sp_dc = self._cookies.get("sp_dc")
        self._session = session or requests.Session()
        self._timeout = timeout
        self._secrets_url = secrets_url

        self._access_token = access_token
        self._expires_at: float | None = None if access_token else 0.0
        self._manual_token = access_token is not None
        self._client_id = WEB_PLAYER_CLIENT_ID

        self._client_token: str | None = None
        self._client_token_expires_at = 0.0

    @property
    def has_cookie_auth(self) -> bool:
        return bool(self._sp_dc)

    @property
    def has_access_token(self) -> bool:
        return bool(self._access_token)

    def get_access_token(self, *, force: bool = False) -> str:
        if not force and self._access_token and not self._is_expired():
            return self._access_token

        if not self._sp_dc:
            raise AuthError(
                "No Spotify access token is available and no sp_dc cookie was "
                "provided. Export fresh cookies from open.spotify.com."
            )

        token = self._fetch_web_player_token()
        self._access_token = token["access_token"]
        self._expires_at = token["expires_at"]
        self._client_id = token.get("client_id") or WEB_PLAYER_CLIENT_ID
        return self._access_token

    def refresh_access_token(self) -> str:
        """Drop the cached token and obtain a new one (requires sp_dc)."""
        self._access_token = None
        self._expires_at = 0.0
        self._client_token = None
        self._client_token_expires_at = 0.0
        if not self._sp_dc:
            if self._manual_token:
                raise AuthError(
                    "Spotify rejected the supplied access token and no sp_dc "
                    "cookie is available to refresh it. Export cookies or pass "
                    "a new --access-token."
                )
            raise AuthError("Spotify rejected the access token; no sp_dc cookie available")
        return self.get_access_token(force=True)

    def get_client_token(self) -> str | None:
        """Return a client token, or ``None`` if Spotify did not grant one.

        The header is not always mandatory, so failures are non-fatal.
        """
        now = time.time()
        if self._client_token and now < self._client_token_expires_at - 60:
            return self._client_token

        payload = {
            "client_data": {
                "client_version": WEB_PLAYER_APP_VERSION,
                "client_id": self._client_id,
                "js_sdk_data": {
                    "device_brand": "unknown",
                    "device_model": "unknown",
                    "os": "mac",
                    "os_version": "10.15.7",
                    "device_id": uuid.uuid4().hex,
                    "device_type": "computer",
                },
            }
        }
        try:
            response = self._session.post(
                CLIENT_TOKEN_URL,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Origin": WEB_PLAYER_HOME.rstrip("/"),
                    "Referer": WEB_PLAYER_HOME,
                },
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()
            granted = data.get("granted_token") or {}
            token = granted.get("token")
            if not isinstance(token, str) or not token:
                return None
            expires_after = granted.get("expires_after_seconds")
            lifetime = (
                expires_after if isinstance(expires_after, (int, float)) else 3600
            )
            self._client_token = token
            self._client_token_expires_at = now + float(lifetime)
            return token
        except (requests.RequestException, ValueError) as exc:
            logger.debug("Could not obtain a Spotify client token: %s", exc)
            return None

    def _is_expired(self) -> bool:
        if self._expires_at is None:
            return False
        return time.time() >= self._expires_at - 60

    def _fetch_web_player_token(self) -> dict[str, Any]:
        secrets = load_secrets(self._session, self._secrets_url, timeout=self._timeout)
        candidates = candidate_secrets(secrets, limit=3)

        last_error: AuthError | None = None
        for version, cipher in candidates:
            code = generate_code(cipher)
            try:
                response = self._session.get(
                    TOKEN_URL,
                    params={
                        "reason": "transport",
                        "productType": "web-player",
                        "totp": code,
                        "totpServer": code,
                        "totpVer": version,
                    },
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "application/json",
                        "Referer": WEB_PLAYER_HOME,
                        "App-Platform": "WebPlayer",
                    },
                    cookies={"sp_dc": self._sp_dc or ""},
                    timeout=self._timeout,
                )
            except requests.RequestException as exc:
                raise AuthError(f"Could not reach Spotify's token endpoint: {exc}") from exc

            if response.status_code == 400 and "invalid totp" in response.text.lower():
                last_error = AuthError(
                    "Spotify rejected the generated TOTP. Its secret list is "
                    "probably out of date; try again in a few minutes."
                )
                logger.debug("Invalid TOTP for secret version %s", version)
                continue

            if response.status_code in {401, 403}:
                raise AuthError(
                    "Spotify rejected the sp_dc cookie. Log in to open.spotify.com "
                    "in your browser and export the cookies again."
                )

            if response.status_code != 200:
                raise AuthError(
                    "Spotify's token endpoint returned HTTP "
                    f"{response.status_code}: {response.text[:300]}"
                )

            try:
                data = response.json()
            except ValueError as exc:
                raise AuthError("Spotify's token endpoint returned invalid JSON") from exc

            if not isinstance(data, dict):
                raise AuthError("Unexpected response from Spotify's token endpoint")

            if data.get("isAnonymous"):
                raise AuthError(
                    "Spotify returned an anonymous token: the sp_dc cookie is "
                    "missing, expired or invalid. Log in to open.spotify.com and "
                    "export fresh cookies."
                )

            access_token = data.get("accessToken")
            if not access_token and isinstance(data.get("data"), dict):
                access_token = data["data"].get("accessToken")
            if not isinstance(access_token, str) or not access_token:
                message = data.get("error") or data.get("message") or data
                raise AuthError(f"Spotify did not return an access token: {message}")

            expires_at = _token_expiry(data)
            return {
                "access_token": access_token,
                "expires_at": expires_at,
                "client_id": data.get("clientId"),
            }

        if last_error:
            raise last_error
        raise AuthError("Could not obtain a Spotify access token")


def _token_expiry(data: dict[str, Any]) -> float:
    raw = data.get("accessTokenExpirationTimestampMs")
    if isinstance(raw, (int, float)) and raw > 0:
        # Spotify reports milliseconds; tolerate a seconds value too.
        return float(raw) / 1000.0 if raw > 10_000_000_000 else float(raw)
    return time.time() + 1800
