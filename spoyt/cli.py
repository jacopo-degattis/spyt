"""Command line interface."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .errors import SpoytError
from .spotify import SpotifyWebClient, load_cookies
from .spotify.constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spoyt",
        description="Move your Spotify playlists to YouTube Music (Spotify side).",
    )
    parser.add_argument("--version", action="version", version=f"spoyt {__version__}")
    parser.add_argument("--debug", action="store_true", help="enable debug logging")

    commands = parser.add_subparsers(dest="command", required=True)

    spotify = commands.add_parser("spotify", help="read data from Spotify")
    spotify_commands = spotify.add_subparsers(dest="spotify_command", required=True)

    playlist = spotify_commands.add_parser(
        "playlist", help="fetch every track of a playlist as JSON"
    )
    playlist.add_argument(
        "reference",
        help="playlist URL, URI (spotify:playlist:...) or bare id",
    )
    playlist.add_argument(
        "--cookies",
        metavar="PATH",
        help="exported cookies.txt/JSON file (use '-' to read from stdin)",
    )
    playlist.add_argument(
        "--access-token",
        help="skip cookie auth and use this bearer token (also SPOYT_SPOTIFY_ACCESS_TOKEN)",
    )
    playlist.add_argument(
        "-o",
        "--out",
        metavar="PATH",
        help="write JSON here instead of stdout",
    )
    playlist.add_argument(
        "--page-size",
        type=_page_size,
        default=DEFAULT_PAGE_SIZE,
        help=f"items per API request (default: {DEFAULT_PAGE_SIZE}, max {MAX_PAGE_SIZE})",
    )
    playlist.add_argument(
        "--max-items",
        type=_positive_int,
        help="only read the first N playlist positions",
    )
    playlist.add_argument(
        "--include-episodes",
        action="store_true",
        help="keep podcast episodes in the track list instead of skipping them",
    )
    playlist.add_argument(
        "--compact",
        action="store_true",
        help="emit compact JSON instead of pretty-printed JSON",
    )
    playlist.add_argument(
        "--debug",
        action="store_true",
        default=argparse.SUPPRESS,
        help="enable debug logging (same as the global option)",
    )
    playlist.add_argument("-q", "--quiet", action="store_true", help="no progress output")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.command == "spotify" and args.spotify_command == "playlist":
        return _fetch_playlist(args)
    parser.error("unknown command")
    return 2


def _fetch_playlist(args: argparse.Namespace) -> int:
    try:
        if args.cookies and args.cookies != "-" and not Path(args.cookies).is_file():
            print(f"error: cookie file not found: {args.cookies}", file=sys.stderr)
            return 2

        cookies = load_cookies(args.cookies) if args.cookies else None
        access_token = args.access_token or os.environ.get("SPOYT_SPOTIFY_ACCESS_TOKEN")

        if not cookies and not access_token:
            print(
                "error: provide --cookies (recommended) or --access-token",
                file=sys.stderr,
            )
            return 2

        with SpotifyWebClient(cookies=cookies, access_token=access_token) as client:
            progress = _make_progress_printer(quiet=args.quiet)
            playlist = client.fetch_playlist(
                args.reference,
                page_size=args.page_size,
                include_episodes=args.include_episodes,
                max_items=args.max_items,
                progress=progress,
            )
            _finish_progress(quiet=args.quiet)

        document = {
            "schema_version": 1,
            "source": "spotify",
            **playlist.to_dict(),
        }
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
                print(_summary(playlist, destination=str(output)), file=sys.stderr)
        else:
            print(text)

        return 0

    except SpoytError as exc:
        _finish_progress(quiet=getattr(args, "quiet", False))
        print(f"error: {exc}", file=sys.stderr)
        if args.debug:
            raise
        return 1
    except OSError as exc:
        _finish_progress(quiet=getattr(args, "quiet", False))
        print(f"error: {exc}", file=sys.stderr)
        if args.debug:
            raise
        return 1
    except KeyboardInterrupt:
        _finish_progress(quiet=getattr(args, "quiet", False))
        print("Interrupted", file=sys.stderr)
        return 130


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


def _summary(playlist, *, destination: str) -> str:
    skipped = len(playlist.skipped)
    suffix = f", {skipped} skipped" if skipped else ""
    name = playlist.name or playlist.uri
    return f'Saved {len(playlist.tracks)} tracks{suffix} from "{name}" to {destination}'


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
