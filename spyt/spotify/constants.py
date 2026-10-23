"""Values that mirror what Spotify's web player sends.

Spotify changes these from time to time. Each one can be overridden with an
environment variable so the tool can be repaired without a code change.
"""

from __future__ import annotations

import os

USER_AGENT = os.environ.get(
    "SPOYT_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:158.0) Gecko/20100101 Firefox/158.0",
)

# The client id used by the open.spotify.com web player.
WEB_PLAYER_CLIENT_ID = os.environ.get(
    "SPOYT_SPOTIFY_CLIENT_ID", "d8a5ed958d274c2e8ee717e6a4b0971d"
)

# Reported both to the token endpoint and to pathfinder.
WEB_PLAYER_APP_VERSION = os.environ.get(
    "SPOYT_SPOTIFY_APP_VERSION", "1.3.8.461.g274622d4ef7d"
)

PATHFINDER_URL = "https://api-partner.spotify.com/pathfinder/v2/query"

# Persisted query hash for the "fetchPlaylist" operation. If Spotify rotates it,
# the client rediscovers the new hash from the web-player bundle automatically.
FETCH_PLAYLIST_HASH = os.environ.get(
    "SPOYT_FETCH_PLAYLIST_HASH",
    "21ea11a6ac75a5f3e01fd365d29c18e0a4a8b7dc09f89bbb5d73910190c6acaf",
)

# The web player asks for an access token through TOTP. The AES-cipher values
# (one per web-player build) are maintained in this community repository.
SECRETS_URL = os.environ.get(
    "SPOYT_SPOTIFY_SECRETS_URL",
    "https://raw.githubusercontent.com/xyloflake/spot-secrets-go/main/secrets/secretDict.json",
)

TOKEN_URL = "https://open.spotify.com/api/token"
CLIENT_TOKEN_URL = "https://clienttoken.spotify.com/v1/clienttoken"
WEB_PLAYER_HOME = "https://open.spotify.com/"

# How many items to ask for in one pathfinder request by default. The API
# accepted limits up to 5000 (returning everything available), but staying
# moderate keeps responses small and memory use predictable.
DEFAULT_PAGE_SIZE = 250
MAX_PAGE_SIZE = 5000

DEFAULT_TIMEOUT = 30.0
