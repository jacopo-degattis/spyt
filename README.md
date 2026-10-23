# Spoyt

Move your personal Spotify playlists to YouTube Music.

No Premium Spotify account is required: Spoyt talks to the same internal
`pathfinder` API as the Spotify web player, authenticated with your exported
browser cookies.

**Current status:** the Spotify side works — it authenticates, fetches playlist
metadata, follows pagination and exports every track to JSON. YouTube Music
matching/import is the next stage.

---

## How it works

1. You export the cookies of a logged-in `open.spotify.com` session.
2. Spoyt takes the long-lived `sp_dc` cookie and exchanges it for a web-player
   access token. Since 2025 that exchange requires a TOTP, which Spoyt generates
   from the per-build secret list maintained by
   [spot-secrets-go](https://github.com/xyloflake/spot-secrets-go).
3. It calls the `fetchPlaylist` pathfinder operation, page by page, and
   normalises the response into a small, stable JSON document.

The old `open.spotify.com/get_access_token` endpoint no longer works; Spoyt
implements the current TOTP flow.

---

## Install

Python 3.10+.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
# for development/tests
.venv/bin/pip install -e '.[dev]'
```

This installs the `spoyt` command inside the virtualenv.

---

## Get your cookies

Personal playlists need your own session cookie.

1. Log in at <https://open.spotify.com> in a normal browser.
2. Export cookies for `open.spotify.com` / `.spotify.com`, for example with
   **Get cookies.txt LOCALLY** (Chrome/Firefox) or **Cookie-Editor** → Export as
   JSON.
3. Save the file as `cookies.txt` (or `cookies.json`) next to the project.

Supported formats:

- Netscape `cookies.txt`
- Cookie-Editor / EditThisCookie JSON array
- Playwright/Puppeteer storage state (`{"cookies": [...]}`)
- plain `sp_dc=...; sp_key=...` header
- a bare `sp_dc` value

> Treat the cookie file like a password: `sp_dc` grants access to your Spotify
> account. It is already ignored by `.gitignore`; never commit or share it.

On macOS, the downloaded file often contains several Spotify cookies. Spoyt
only needs `sp_dc`, and ignores cookies for other domains.

---

## Usage

```bash
# URL, URI or bare playlist id all work
.venv/bin/spoyt spotify playlist \
  'https://open.spotify.com/playlist/6yNRwWCVVNLEsif1esJ1Cc' \
  --cookies cookies.txt \
  --out playlist.json
```

Output goes to stdout when `--out` is omitted, so it can be piped:

```bash
.venv/bin/spoyt spotify playlist spotify:playlist:6yNRwWCVVNLEsif1esJ1Cc \
  --cookies cookies.txt > playlist.json
```

Useful options:

| Option | Description |
| --- | --- |
| `--cookies PATH` | Exported cookies file; `-` reads stdin |
| `--access-token TOKEN` | Use a bearer token directly (debugging fallback) |
| `-o, --out PATH` | Write JSON to a file instead of stdout |
| `--page-size N` | Items per request (default 250, max 5000) |
| `--max-items N` | Only read the first N playlist positions |
| `--include-episodes` | Keep podcast episodes in `tracks` (default: skip) |
| `--compact` | Minified JSON |
| `-q, --quiet` | Hide progress/summary messages |
| `--debug` | Show debug logs and tracebacks |

You can also set `SPOYT_SPOTIFY_ACCESS_TOKEN` instead of passing the token on
the command line.

### Python API

```python
from spoyt import SpotifyWebClient, load_cookies

cookies = load_cookies("cookies.txt")

with SpotifyWebClient(cookies=cookies) as client:
    playlist = client.fetch_playlist(
        "https://open.spotify.com/playlist/6yNRwWCVVNLEsif1esJ1Cc",
        page_size=500,
    )

print(playlist.name, len(playlist.tracks))
for track in playlist.tracks:
    print(track.position, track.name, "—", ", ".join(a.name for a in track.artists))
```

---

## Output format

```json
{
  "schema_version": 1,
  "source": "spotify",
  "id": "6yNRwWCVVNLEsif1esJ1Cc",
  "uri": "spotify:playlist:6yNRwWCVVNLEsif1esJ1Cc",
  "name": "Certified",
  "description": "Only english Rap, Trap and Hip-Hop songs.",
  "owner": {
    "name": "Jack",
    "uri": "spotify:user:...",
    "username": "..."
  },
  "cover_url": "https://...",
  "total_items": 1021,
  "revision_id": "AAAEM0w...",
  "fetched_at": "2026-10-23T17:55:20+00:00",
  "track_count": 1021,
  "tracks": [
    {
      "position": 1,
      "uri": "spotify:track:7pudclCl5P99Me3P5F9hPC",
      "id": "7pudclCl5P99Me3P5F9hPC",
      "name": "Paradise",
      "kind": "track",
      "artists": [{ "name": "Lil Uzi Vert", "uri": "spotify:artist:..." }],
      "album": {
        "name": "Luv Is Rage",
        "uri": "spotify:album:...",
        "cover_url": "https://...",
        "release_date": "2015-12-18T00:00:00Z"
      },
      "duration_ms": 172703,
      "explicit": false,
      "playable": true,
      "local": false,
      "added_at": "2023-03-08T18:05:38.970Z",
      "added_by": "Jack",
      "playcount": 73485725
    }
  ],
  "skipped": []
}
```

`position` is the 1-based position in the original Spotify playlist. Tracks
that cannot be represented (currently unavailable items, and podcast episodes
unless `--include-episodes` is used) are listed in `skipped` with a `reason`.

---

## Pathfinder notes

- Request body follows the web player:

  ```json
  {
    "variables": {
      "uri": "spotify:playlist:<id>",
      "offset": 0,
      "limit": 250,
      "enableWatchFeedEntrypoint": true,
      "includeEpisodeContentRatingsV2": true
    },
    "operationName": "fetchPlaylist",
    "extensions": {
      "persistedQuery": { "version": 1, "sha256Hash": "..." }
    }
  }
  ```

- **Page size:** during live testing (October 2026) Spotify accepted limits up
  to **5000** and returned every available item, so there is no small hard cap
  like 100. Spoyt defaults to 250 to keep responses and memory reasonable;
  `--page-size 1000` is fine for large playlists.
- Pagination uses `content.totalCount`; requests stop when the offset reaches
  it or a page comes back empty.
- If Spotify rotates the persisted-query hash, Spoyt redownloads the web-player
  JS bundle and extracts the new `fetchPlaylist` hash automatically. As a last
  resort, set `SPOYT_FETCH_PLAYLIST_HASH`.
- Other overridable environment variables: `SPOYT_SPOTIFY_CLIENT_ID`,
  `SPOYT_SPOTIFY_APP_VERSION`, `SPOYT_USER_AGENT`,
  `SPOYT_SPOTIFY_SECRETS_URL`.

The client handles `401` (refreshes the token from `sp_dc`), `429`
(honours `Retry-After`) and transient `5xx` responses with backoff.

---

## Tests

The suite is offline and uses synthetic pathfinder payloads:

```bash
.venv/bin/python -m pytest -q
```

---

## Roadmap

1. Spotify export — **done**
2. YouTube Music authentication (browser cookies)
3. Track matching (search by title/artist/duration, confidence scoring)
4. Playlist creation/import with resumable progress
5. Review UI/CLI for ambiguous matches
