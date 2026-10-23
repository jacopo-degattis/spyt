# Spyt

Move your personal Spotify playlists to YouTube Music.

No Premium Spotify account is required: Spyt talks to the same internal
`pathfinder` API as the Spotify web player, authenticated with your exported
browser cookies.

**Current status:** the full pipeline works — Spyt authenticates to Spotify and
YouTube Music, reads both libraries, matches Spotify tracks to YouTube Music
songs, and creates the destination playlist (private by default) with resumable
JSON reports.

---

## How it works

1. You export the cookies of a logged-in `open.spotify.com` session.
2. Spyt takes the long-lived `sp_dc` cookie and exchanges it for a web-player
   access token. Since 2025 that exchange requires a TOTP, which Spyt generates
   from the per-build secret list maintained by
   [spot-secrets-go](https://github.com/xyloflake/spot-secrets-go).
3. It calls the `fetchPlaylist` pathfinder operation, page by page, and
   normalises the response into a small, stable JSON document.
4. For YouTube Music, you paste the request headers of an authenticated
   `music.youtube.com` request. Spyt builds a session from them and reads the
   playlist through the same internal `browse` API as the web player.
5. The transfer stage searches YouTube Music for every Spotify track, scores
   candidates by title, artist and duration, and adds the best matches to a new
   playlist. The mapping report can be inspected and resumed.
6. Both readers still produce separate, stable JSON documents for scripting.

The old `open.spotify.com/get_access_token` endpoint no longer works; Spyt
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

This installs the `spyt` command inside the virtualenv.

---

## Get your Spotify cookies

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

On macOS, the downloaded file often contains several Spotify cookies. Spyt
only needs `sp_dc`, and ignores cookies for other domains.

---

## Get your YouTube Music headers

Private library playlists need an authenticated YouTube Music session. Spyt
uses the copied-request-headers approach used by other YouTube Music tools:

1. Log in at <https://music.youtube.com>.
2. Open DevTools → **Network**, then open any playlist in the app.
3. Find the `browse?prettyPrint=false` request (filter by `browse`).
4. Right-click it → **Copy** → **Copy request headers** (or **Copy as cURL**).
5. Paste everything into `ytmusic_headers.txt`.

Supported formats for that file:

- DevTools "Copy request headers" (`name: value` lines, including Chrome's
  two-line variant)
- "Copy as cURL" (single or multi-line)
- a JSON object of header names to values
- a raw HTTP request line followed by the headers

Spyt only needs the cookies, `X-Goog-AuthUser` and a working browser session. On
startup it reads the account's `DATASYNC_ID` from the YouTube Music page and
regenerates the browser-style triple authorization header
(`SAPISIDHASH` / `SAPISID1PHASH` / `SAPISID3PHASH`). It also verifies that the
session is still logged in (`LOGGED_IN=true`), so stale headers fail with a
clear message instead of silently producing an empty playlist.

> The headers file contains your Google session cookies. It is ignored by
> `.gitignore`; never commit or share it.

Public playlists work without a headers file. For private playlists, copy the
headers from a request made while logged in.

---

## Usage

### Transfer a Spotify playlist to YouTube Music

This is the main workflow. It reads the Spotify playlist, searches YouTube
Music for each track, creates a new playlist and adds the matches:

```bash
.venv/bin/spyt transfer \
  'https://open.spotify.com/playlist/6yNRwWCVVNLEsif1esJ1Cc' \
  --cookies cookies.txt \
  --headers ytmusic_headers.txt \
  --privacy private \
  -o transfer-report.json
```

Defaults: the new playlist is **private** and uses the Spotify playlist name.
Use `--name` and `--description` to override them. Spyt asks for confirmation
before writing anything; pass `-y/--yes` for non-interactive runs.

Recommended for large playlists: match first, review, then create. A dry run
writes the same report without touching YouTube Music, and the second run
reuses every cached match instead of searching again:

```bash
# 1. match only
.venv/bin/spyt transfer 'spotify:playlist:6yNRwWCVVNLEsif1esJ1Cc' \
  --cookies cookies.txt --headers ytmusic_headers.txt \
  --dry-run -o transfer-report.json

# 2. create the playlist, reusing the report
.venv/bin/spyt transfer 'spotify:playlist:6yNRwWCVVNLEsif1esJ1Cc' \
  --cookies cookies.txt --headers ytmusic_headers.txt \
  -o transfer-report.json
```

How matching works:

- Each track is searched in the YouTube Music **songs** catalogue, then in
  **videos** if no album upload exists.
- Candidates are scored on title similarity (50%), artist similarity (35%) and
  duration closeness (15%), with a bonus for official album tracks.
- Covers, karaoke, instrumental, sped-up, live, remix and acoustic variants are
  penalised, and results whose duration differs by more than ~12% are rejected.
- The default threshold is `0.80`; lower it with `--min-score` or re-run with
  `--dry-run` to review candidates manually.
- Tracks already matched in the report are reused (`--rematch` searches again),
  while unmatched tracks are retried automatically.
- Duplicate video ids are added once; later positions are marked `duplicate`.

`transfer-report.json` records every Spotify track, the chosen YouTube video,
the confidence score and a reason for anything skipped or unmatched, so a large
transfer can be reviewed, resumed or scripted.

Transfer-specific options:

| Option | Description |
| --- | --- |
| `--name TITLE` | Name of the new YouTube Music playlist |
| `--description TEXT` | Description of the new playlist |
| `--privacy {private,unlisted,public}` | Visibility (default: `private`) |
| `--min-score 0.0-1.0` | Match confidence threshold (default: `0.80`) |
| `--dry-run` | Match tracks and write the report; create nothing |
| `--rematch` | Ignore cached matches in the `--out` report |
| `-y, --yes` | Skip the creation confirmation |
| `--no-verify` | Skip the YouTube logged-in pre-check |

The reader commands below are still available when you only need raw JSON.

### Spotify

```bash
# URL, URI or bare playlist id all work
.venv/bin/spyt spotify playlist \
  'https://open.spotify.com/playlist/6yNRwWCVVNLEsif1esJ1Cc' \
  --cookies cookies.txt \
  --out spotify-playlist.json
```

Output goes to stdout when `--out` is omitted, so it can be piped:

```bash
.venv/bin/spyt spotify playlist spotify:playlist:6yNRwWCVVNLEsif1esJ1Cc \
  --cookies cookies.txt > spotify-playlist.json
```

### YouTube Music

```bash
.venv/bin/spyt youtube playlist \
  'https://music.youtube.com/playlist?list=PLQwVIlKxHM6qv-o99iX9R85og7IzF9YS_' \
  --headers ytmusic_headers.txt \
  --out youtube-playlist.json
```

Omit `--headers` for a public playlist:

```bash
.venv/bin/spyt youtube playlist PLQwVIlKxHM6qv-o99iX9R85og7IzF9YS_
```

Useful options:

| Option | Description |
| --- | --- |
| `--cookies PATH` | Spotify cookies file; `-` reads stdin |
| `--access-token TOKEN` | Spotify bearer token (debugging fallback) |
| `--headers PATH` | YouTube Music copied request headers; `-` reads stdin |
| `-o, --out PATH` | Write JSON to a file instead of stdout |
| `--page-size N` | Spotify items per request (default 250, max 5000) |
| `--max-items N` | Only read the first N playlist positions |
| `--include-episodes` | Keep podcast episodes in `tracks` (default: skip) |
| `--no-verify` | Skip the YouTube logged-in pre-check (advanced) |
| `--compact` | Minified JSON |
| `-q, --quiet` | Hide progress/summary messages |
| `--debug` | Show debug logs and tracebacks |

You can also set `SPYT_SPOTIFY_ACCESS_TOKEN` instead of passing the Spotify
token on the command line.

### Python API

```python
from spyt import SpotifyWebClient, load_cookies

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

```python
from spyt.youtube import YouTubeMusicClient, load_headers

headers = load_headers("ytmusic_headers.txt")  # or None for public playlists

client = YouTubeMusicClient(headers=headers)
playlist = client.fetch_playlist(
    "https://music.youtube.com/playlist?list=PLQwVIlKxHM6qv-o99iX9R85og7IzF9YS_"
)

print(playlist.title, len(playlist.tracks))
for track in playlist.tracks:
    print(track.position, track.title, "—", ", ".join(a.name for a in track.artists))
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

YouTube Music documents use the same envelope with `"source": "youtube_music"`:

```json
{
  "schema_version": 1,
  "source": "youtube_music",
  "id": "PLQwVIlKxHM6qv-o99iX9R85og7IzF9YS_",
  "url": "https://music.youtube.com/playlist?list=PLQwVIlKxHM6qv-o99iX9R85og7IzF9YS_",
  "title": "New EDM This Week 03/13/2020",
  "description": "...",
  "owner": { "name": "sigmatics", "id": "UC..." },
  "year": "2021",
  "privacy": "PUBLIC",
  "cover_url": "https://...",
  "reported_track_count": 236,
  "duration_text": "14+ hours",
  "duration_seconds": 52922,
  "fetched_at": "2026-10-23T18:10:49+00:00",
  "track_count": 233,
  "tracks": [
    {
      "position": 1,
      "video_id": "ekz1IJ9I0sw",
      "title": "Lost",
      "kind": "song",
      "artists": [{ "name": "Guest Who", "id": "UC..." }],
      "album": { "name": "Lost", "id": "MPREb_..." },
      "duration_ms": 179000,
      "duration_text": "2:59",
      "explicit": false,
      "available": true,
      "video_type": "MUSIC_VIDEO_TYPE_ATV",
      "set_video_id": null,
      "cover_url": "https://..."
    }
  ],
  "skipped": []
}
```

`reported_track_count` is the count YouTube Music displays in the header;
`track_count` is what was actually returned. Deleted/unplayable entries can
make the latter smaller. `video_id` is what the import stage will add to a
playlist; `set_video_id` is only present for playlists you own and is useful for
reordering/removal.

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
  like 100. Spyt defaults to 250 to keep responses and memory reasonable;
  `--page-size 1000` is fine for large playlists.
- Pagination uses `content.totalCount`; requests stop when the offset reaches
  it or a page comes back empty.
- If Spotify rotates the persisted-query hash, Spyt redownloads the web-player
  JS bundle and extracts the new `fetchPlaylist` hash automatically. As a last
  resort, set `SPYT_FETCH_PLAYLIST_HASH`.
- Other overridable environment variables: `SPYT_SPOTIFY_CLIENT_ID`,
  `SPYT_SPOTIFY_APP_VERSION`, `SPYT_USER_AGENT`,
  `SPYT_SPOTIFY_SECRETS_URL`.

The client handles `401` (refreshes the token from `sp_dc`), `429`
(honours `Retry-After`) and transient `5xx` responses with backoff.

---

## YouTube Music notes

- The low-level YouTube Music protocol is handled by
  [ytmusicapi](https://github.com/sigma67/ytmusicapi); Spyt owns the header-file
  parsing, error mapping, models and CLI so the two providers stay separate.
- YouTube Music returns **100 items per page**. Pagination follows continuation
  tokens and has no configurable page size; `--max-items` only caps the output.
- If you pass a headers file, Spyt:
  - strips hop-by-hop/browser headers (`host`, `accept-encoding`, `sec-*`, ...);
  - keeps your cookies;
  - regenerates `SAPISIDHASH` from `__Secure-3PAPISID` and `Origin`;
  - defaults `X-Goog-AuthUser` to `0` when it is absent.
- Both `https://music.youtube.com/playlist?list=...` and
  `https://music.youtube.com/watch?v=...&list=...` URLs are accepted, as are
  bare ids and `VL...` browse ids.
- `kind` is derived from `video_type`: `song` (album track/ATV), `video` (OMV,
  UGC), `episode` (podcasts) or `unknown`. Episodes are skipped by default.
- Creating and editing playlists requires authenticated headers; anonymous
  access is read-only. Adds are batched (50 videos per request) and transient
  `429`/`5xx` responses are retried with backoff.

---

## Tests

The suite is offline and uses synthetic payloads for both providers. Matching
tests use fake search results, so no API access is needed:

```bash
.venv/bin/python -m pytest -q
```

---

## Roadmap

1. Spotify export — **done**
2. YouTube Music authentication and export — **done**
3. Track matching (title/artist/duration, confidence scoring) — **done**
4. Playlist creation/import with resumable progress — **done**
5. Review UI/CLI for ambiguous matches — **partial** (`--dry-run` report)
