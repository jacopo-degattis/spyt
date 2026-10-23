from __future__ import annotations

import pytest

from spyt.cli import build_parser


def test_transfer_defaults():
    args = build_parser().parse_args(
        [
            "transfer",
            "spotify:playlist:abc",
            "--cookies",
            "cookies.txt",
            "--headers",
            "headers.txt",
        ]
    )
    assert args.command == "transfer"
    assert args.reference == "spotify:playlist:abc"
    assert args.privacy == "private"
    assert args.min_score == 0.8
    assert args.dry_run is False
    assert args.rematch is False
    assert args.yes is False
    assert args.out is None


def test_transfer_dry_run_needs_no_headers_at_parse_time():
    args = build_parser().parse_args(
        ["transfer", "abc", "--cookies", "cookies.txt", "--dry-run"]
    )
    assert args.headers is None
    assert args.dry_run is True


def test_transfer_rejects_invalid_min_score():
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["transfer", "abc", "--cookies", "cookies.txt", "--min-score", "1.5"]
        )


def test_transfer_rejects_invalid_privacy():
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["transfer", "abc", "--cookies", "cookies.txt", "--privacy", "secret"]
        )
