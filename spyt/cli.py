"""Command line interface for Spyt."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .errors import SpytError
from .match import DEFAULT_MIN_SCORE
from .spotify import SpotifyWebClient, load_cookies
from .spotify.constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from .transfer import (
    build_report,
    create_target_playlist,
    load_report,
    mark_duplicates,
    match_playlist,
    resume_index,
    write_report,
)
from .youtube import YouTubeMusicClient, load_headers

SOURCE_SPOTIFY = "spotify"
SOURCE_YOUTUBE = "youtube_music"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spyt",
        description="Move your Spotify playlists to YouTube Music.",
    )
    parser.add_argument("--version", action="version", version=f"spyt {__version__}")
    parser.add_argument("--debug", action="store_true", help="enable debug logging")

    commands = parser.add_subparsers(dest="command", required=True)

    # --- Spotify -----------------------------------------------------------
    spotify = commands.add_parser("spotify", help="read data from Spotify")
    spotify_commands = spotify.add_subparsers(dest="spotify_command", required=True)

    spotify_playlist = spotify_commands.add_parser(
        "playlist", help="fetch every track of a Spotify playlist as JSON"
    )
    spotify_playlist.add_argument(
        "reference",
        help="playlist URL, URI (spotify:playlist:...) or bare id",
    )
    spotify_playlist.add_argument(
        "--cookies",
        metavar="PATH",
        help="exported cookies.txt/JSON file (use '-' to read from stdin)",
    )
    spotify_playlist.add_argument(
        "--access-token",
        help="skip cookie auth and use this bearer token (also SPYT_SPOTIFY_ACCESS_TOKEN)",
    )
    spotify_playlist.add_argument(
        "--page-size",
        type=_page_size,
        default=DEFAULT_PAGE_SIZE,
        help=f"items per API request (default: {DEFAULT_PAGE_SIZE}, max {MAX_PAGE_SIZE})",
    )
    spotify_playlist.add_argument(
        "--max-items",
        type=_positive_int,
        help="only read the first N playlist positions",
    )
    spotify_playlist.add_argument(
        "--include-episodes",
        action="store_true",
        help="keep podcast episodes in the track list instead of skipping them",
    )
    _add_output_options(spotify_playlist)

    # --- YouTube Music -----------------------------------------------------
    youtube = commands.add_parser("youtube", help="read data from YouTube Music")
    youtube_commands = youtube.add_subparsers(dest="youtube_command", required=True)

    youtube_playlist = youtube_commands.add_parser(
        "playlist", help="fetch every track of a YouTube Music playlist as JSON"
    )
    youtube_playlist.add_argument(
        "reference",
        help="playlist URL or bare playlist id",
    )
    youtube_playlist.add_argument(
        "--headers",
        metavar="PATH",
        help=(
            "authenticated request headers copied from music.youtube.com "
            "(headers, cURL or JSON; use '-' to read from stdin). Omit for public playlists."
        ),
    )
    youtube_playlist.add_argument(
        "--max-items",
        type=_positive_int,
        help="only read the first N playlist positions",
    )
    youtube_playlist.add_argument(
        "--include-episodes",
        action="store_true",
        help="keep podcast episodes in the track list instead of skipping them",
    )
    youtube_playlist.add_argument(
        "--no-verify",
        action="store_true",
        help="skip the logged-in pre-check and try the headers as-is",
    )
    _add_output_options(youtube_playlist)

    # --- Transfer ----------------------------------------------------------
    transfer = commands.add_parser(
        "transfer",
        help="copy a Spotify playlist to YouTube Music",
    )
    transfer.add_argument(
        "reference",
        help="Spotify playlist URL, URI (spotify:playlist:...) or bare id",
    )
    transfer.add_argument(
        "--cookies",
        metavar="PATH",
        help="exported Spotify cookies.txt/JSON file (use '-' to read from stdin)",
    )
    transfer.add_argument(
        "--access-token",
        help="skip cookie auth and use this bearer token (also SPYT_SPOTIFY_ACCESS_TOKEN)",
    )
    transfer.add_argument(
        "--headers",
        metavar="PATH",
        help=(
            "authenticated request headers copied from music.youtube.com "
            "(required to create the playlist; use '-' to read from stdin)"
        ),
    )
    transfer.add_argument(
        "--name",
        help="title for the new YouTube Music playlist (default: the Spotify name)",
    )
    transfer.add_argument(
        "--description",
        help="description for the new playlist",
    )
    transfer.add_argument(
        "--privacy",
        choices=("private", "unlisted", "public"),
        default="private",
        help="visibility of the new playlist (default: private)",
    )
    transfer.add_argument(
        "--min-score",
        type=_score,
        default=DEFAULT_MIN_SCORE,
        help=f"minimum match confidence from 0 to 1 (default: {DEFAULT_MIN_SCORE})",
    )
    transfer.add_argument(
        "--page-size",
        type=_page_size,
        default=DEFAULT_PAGE_SIZE,
        help=f"Spotify items per API request (default: {DEFAULT_PAGE_SIZE}, max {MAX_PAGE_SIZE})",
    )
    transfer.add_argument(
        "--max-items",
        type=_positive_int,
        help="only transfer the first N playlist positions",
    )
    transfer.add_argument(
        "--dry-run",
        action="store_true",
        help="match tracks and write the report without creating a playlist",
    )
    transfer.add_argument(
        "--rematch",
        action="store_true",
        help="ignore matches cached in an existing --out report",
    )
    transfer.add_argument(
        "--no-verify",
        action="store_true",
        help="skip the YouTube logged-in pre-check and try the headers as-is",
    )
    transfer.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="do not ask for confirmation before creating the playlist",
    )
    _add_output_options(transfer)

    # --- Web UI ------------------------------------------------------------
    web = commands.add_parser(
        "web",
        help="run the local browser UI",
    )
    web.add_argument(
        "--host",
        default="127.0.0.1",
        help="interface to bind (default: 127.0.0.1; use 0.0.0.0 to expose it)",
    )
    web.add_argument(
        "--port",
        type=_port,
        default=8000,
        help="port to listen on (default: 8000)",
    )
    web.add_argument(
        "--no-open",
        action="store_true",
        help="do not open the browser automatically",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        if args.command == "spotify" and args.spotify_command == "playlist":
            return _fetch_spotify_playlist(args)
        if args.command == "youtube" and args.youtube_command == "playlist":
            return _fetch_youtube_playlist(args)
        if args.command == "transfer":
            return _transfer(args)
        if args.command == "web":
            return _serve_web(args)

        parser.error("unknown command")
        return 2

    except SpytError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if args.debug:
            raise
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if args.debug:
            raise
        return 1
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130


def _fetch_spotify_playlist(args: argparse.Namespace) -> int:
    if args.cookies and args.cookies != "-" and not Path(args.cookies).is_file():
        print(f"error: cookie file not found: {args.cookies}", file=sys.stderr)
        return 2

    cookies = load_cookies(args.cookies) if args.cookies else None
    access_token = args.access_token or os.environ.get("SPYT_SPOTIFY_ACCESS_TOKEN")

    if not cookies and not access_token:
        print(
            "error: provide --cookies (recommended) or --access-token",
            file=sys.stderr,
        )
        return 2

    try:
        with SpotifyWebClient(cookies=cookies, access_token=access_token) as client:
            playlist = client.fetch_playlist(
                args.reference,
                page_size=args.page_size,
                include_episodes=args.include_episodes,
                max_items=args.max_items,
                progress=_make_progress_printer(quiet=args.quiet),
            )
    finally:
        _finish_progress(quiet=args.quiet)

    _emit_document(
        {
            "schema_version": 1,
            "source": SOURCE_SPOTIFY,
            **playlist.to_dict(),
        },
        args,
        summary=_summary(
            len(playlist.tracks),
            len(playlist.skipped),
            playlist.name or playlist.uri,
        ),
    )
    return 0


def _fetch_youtube_playlist(args: argparse.Namespace) -> int:
    if args.headers and args.headers != "-" and not Path(args.headers).is_file():
        print(f"error: headers file not found: {args.headers}", file=sys.stderr)
        return 2

    headers = load_headers(args.headers) if args.headers else None
    client = YouTubeMusicClient(headers=headers, verify_session=not args.no_verify)
    playlist = client.fetch_playlist(
        args.reference,
        include_episodes=args.include_episodes,
        max_items=args.max_items,
    )

    _emit_document(
        {
            "schema_version": 1,
            "source": SOURCE_YOUTUBE,
            **playlist.to_dict(),
        },
        args,
        summary=_summary(
            len(playlist.tracks),
            len(playlist.skipped),
            playlist.title or playlist.url,
        ),
    )
    return 0


def _transfer(args: argparse.Namespace) -> int:
    if args.cookies and args.cookies != "-" and not Path(args.cookies).is_file():
        print(f"error: cookie file not found: {args.cookies}", file=sys.stderr)
        return 2
    if args.headers and args.headers != "-" and not Path(args.headers).is_file():
        print(f"error: headers file not found: {args.headers}", file=sys.stderr)
        return 2

    cookies = load_cookies(args.cookies) if args.cookies else None
    access_token = args.access_token or os.environ.get("SPYT_SPOTIFY_ACCESS_TOKEN")
    if not cookies and not access_token:
        print(
            "error: provide --cookies (recommended) or --access-token",
            file=sys.stderr,
        )
        return 2

    if not args.headers and not args.dry_run:
        print(
            "error: creating a playlist needs --headers from a logged-in "
            "music.youtube.com session (or use --dry-run to only match tracks)",
            file=sys.stderr,
        )
        return 2

    previous = load_report(args.out) if args.out else None
    if previous and not args.rematch and not args.quiet:
        print(
            f"Resuming matches from {args.out} "
            f"(pass --rematch to search everything again)",
            file=sys.stderr,
        )

    with SpotifyWebClient(cookies=cookies, access_token=access_token) as spotify:
        playlist = spotify.fetch_playlist(
            args.reference,
            page_size=args.page_size,
            progress=_make_progress_printer(quiet=args.quiet),
        )
    _finish_progress(quiet=args.quiet)

    headers = load_headers(args.headers) if args.headers else None
    youtube = YouTubeMusicClient(headers=headers, verify_session=not args.no_verify)

    matches = match_playlist(
        playlist,
        youtube.search_tracks,
        min_score=args.min_score,
        max_items=args.max_items,
        resume=resume_index(previous),
        rematch=args.rematch,
        progress=_make_match_printer(quiet=args.quiet),
    )
    _finish_progress(quiet=args.quiet)
    duplicates = mark_duplicates(matches)
    report = build_report(playlist, matches)

    _print_transfer_summary(report, duplicates=duplicates, quiet=args.quiet)

    if args.dry_run:
        _emit_transfer_report(report, args)
        return 0

    if report.summary["matched"] == 0:
        print("error: no tracks matched; nothing to add", file=sys.stderr)
        _emit_transfer_report(report, args)
        return 1

    title = args.name or playlist.name or f"Spotify playlist {playlist.id}"
    description = args.description or (
        f"Imported from Spotify: https://open.spotify.com/playlist/{playlist.id}"
    )

    if not args.yes and not _confirm(
        f'Create {args.privacy} YouTube Music playlist "{title}" '
        f'with {report.summary["matched"]} tracks? [y/N] '
    ):
        print("Aborted; nothing was created.", file=sys.stderr)
        _emit_transfer_report(report, args)
        return 1

    try:
        added = create_target_playlist(
            youtube,
            report,
            title=title,
            description=description,
            privacy=args.privacy,
            progress=_make_add_printer(quiet=args.quiet),
        )
    except Exception:
        report.status = "failed"
        if args.out:
            write_report(report, args.out, compact=args.compact)
        _finish_progress(quiet=args.quiet)
        raise
    _finish_progress(quiet=args.quiet)

    if not args.quiet:
        print(
            f'Created "{title}" with {added} tracks -> {report.target["url"]}',
            file=sys.stderr,
        )
    _emit_transfer_report(report, args)
    return 0


def _serve_web(args: argparse.Namespace) -> int:
    try:
        from .web.app import run
    except ImportError:
        print(
            "error: the web UI needs optional dependencies.\n"
            "Install them with: .venv/bin/pip install -e '.[web]'",
            file=sys.stderr,
        )
        return 2

    try:
        run(
            host=args.host,
            port=args.port,
            open_browser=not args.no_open,
            log_level="debug" if args.debug else "warning",
        )
    except OSError as exc:
        print(f"error: could not start the web server: {exc}", file=sys.stderr)
        if args.debug:
            raise
        return 1
    return 0


def _emit_transfer_report(report, args: argparse.Namespace) -> None:
    if args.out:
        output = write_report(report, args.out, compact=args.compact)
        if not args.quiet:
            print(f"Report -> {output}", file=sys.stderr)
        return

    print(
        json.dumps(
            report.to_dict(),
            ensure_ascii=False,
            indent=None if args.compact else 2,
        )
    )


def _print_transfer_summary(report, *, duplicates: int, quiet: bool) -> None:
    if quiet:
        return
    summary = report.summary
    parts = [f'{summary["matched"]} matched']
    if summary["unmatched"]:
        parts.append(f'{summary["unmatched"]} unmatched')
    if summary["skipped"]:
        parts.append(f'{summary["skipped"]} skipped')
    if duplicates:
        parts.append(f"{duplicates} duplicates")
    print(f'Matched {summary["spotify_tracks"]} Spotify tracks: ' + ", ".join(parts), file=sys.stderr)

    low_confidence = [track for track in report.tracks if track.status == "unmatched"]
    for track in low_confidence[:10]:
        artists = ", ".join(track.spotify.get("artists") or [])
        detail = f'{track.spotify.get("name")}' + (f" — {artists}" if artists else "")
        if track.youtube:
            detail += (
                f' (best: "{track.youtube.get("title")}", '
                f'score {track.score:.2f})'
            )
        elif track.reason:
            detail += f" ({track.reason})"
        print(f"  unmatched: {detail}", file=sys.stderr)
    if len(low_confidence) > 10:
        print(f"  ... and {len(low_confidence) - 10} more", file=sys.stderr)


def _confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        print(
            "error: confirmation requires a terminal; pass --yes to create the "
            "playlist non-interactively",
            file=sys.stderr,
        )
        return False
    try:
        answer = input(prompt)
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


def _make_match_printer(*, quiet: bool):
    if quiet:
        return None

    def report(processed: int, total: int) -> None:
        if not sys.stderr.isatty():
            return
        sys.stderr.write(f"\rMatched {processed}/{total} tracks")
        sys.stderr.flush()

    return report


def _make_add_printer(*, quiet: bool):
    if quiet:
        return None

    def report(added: int, total: int) -> None:
        if not sys.stderr.isatty():
            return
        sys.stderr.write(f"\rAdded {added}/{total} tracks")
        sys.stderr.flush()

    return report


def _emit_document(
    document: dict[str, Any],
    args: argparse.Namespace,
    *,
    summary: str,
) -> None:
    text = json.dumps(
        document,
        ensure_ascii=False,
        indent=None if args.compact else 2,
    )

    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
        if not args.quiet:
            print(f"{summary} -> {output}", file=sys.stderr)
    else:
        print(text)


def _add_output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-o",
        "--out",
        metavar="PATH",
        help="write JSON here instead of stdout",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="emit compact JSON instead of pretty-printed JSON",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=argparse.SUPPRESS,
        help="enable debug logging (same as the global option)",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="no progress output")


def _page_size(value: str) -> int:
    size = _positive_int(value)
    if size > MAX_PAGE_SIZE:
        raise argparse.ArgumentTypeError(f"must be at most {MAX_PAGE_SIZE}")
    return size


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def _score(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number between 0 and 1") from exc
    if not 0.0 <= number <= 1.0:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return number


def _port(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a port number") from exc
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("must be between 1 and 65535")
    return number


def _summary(track_count: int, skipped: int, name: str) -> str:
    suffix = f", {skipped} skipped" if skipped else ""
    return f'Saved {track_count} tracks{suffix} from "{name}"'


def _make_progress_printer(*, quiet: bool):
    if quiet:
        return None

    def report(processed: int, total: int) -> None:
        if not sys.stderr.isatty():
            return
        sys.stderr.write(f"\rFetched {processed}/{total} items")
        sys.stderr.flush()

    return report


def _finish_progress(*, quiet: bool) -> None:
    if quiet or not sys.stderr.isatty():
        return
    sys.stderr.write("\n")
    sys.stderr.flush()


if __name__ == "__main__":
    raise SystemExit(main())
