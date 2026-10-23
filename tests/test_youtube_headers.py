import hashlib
import json

import pytest

from spyt.errors import HeaderError
from spyt.youtube.headers import parse_headers, sapisid_hash

COOKIE = "SOCS=CAI; __Secure-3PAPISID=abc123; SID=secret"
NOW = 1_700_000_000.0


def _expected_hash() -> str:
    digest = hashlib.sha1(b"1700000000 abc123 https://music.youtube.com").hexdigest()
    return f"SAPISIDHASH 1700000000_{digest}"


def test_raw_headers_preserve_authorization():
    headers = parse_headers(
        f"""accept: */*
authorization: SAPISIDHASH 1_stale
cookie: {COOKIE}
host: music.youtube.com
origin: https://music.youtube.com
sec-fetch-mode: cors
x-goog-authuser: 0
x-youtube-client-name: 67
""",
        now=NOW,
    )

    assert headers["authorization"] == "SAPISIDHASH 1_stale"
    assert headers["cookie"] == COOKIE
    assert headers["x-goog-authuser"] == "0"
    assert headers["x-youtube-client-name"] == "67"
    assert headers["content-type"] == "application/json"
    assert "host" not in headers
    assert "sec-fetch-mode" not in headers


def test_chrome_two_line_header_format():
    headers = parse_headers(
        f"""accept
*/*
cookie:
{COOKIE}
x-goog-authuser
1
""",
        now=NOW,
    )

    assert headers["accept"] == "*/*"
    assert headers["cookie"] == COOKIE
    assert headers["x-goog-authuser"] == "1"
    assert headers["authorization"] == _expected_hash()


def test_curl_headers():
    text = f"""curl 'https://music.youtube.com/youtubei/v1/browse?prettyPrint=false' \\
  -H 'accept: */*' \\
  -H 'authorization: SAPISIDHASH 1_stale' \\
  -H 'cookie: {COOKIE}' \\
  -H 'origin: https://music.youtube.com' \\
  --data-raw '{{"context":{{}}}}'"""
    headers = parse_headers(text, now=NOW)

    assert headers["authorization"] == "SAPISIDHASH 1_stale"
    assert headers["cookie"] == COOKIE
    assert headers["accept"] == "*/*"


def test_json_headers():
    text = json.dumps(
        {
            "headers": {
                "cookie": COOKIE,
                "authorization": "SAPISIDHASH 1_stale",
                "origin": "https://music.youtube.com",
                "x-goog-authuser": "2",
            }
        }
    )
    headers = parse_headers(text, now=NOW)

    assert headers["authorization"] == "SAPISIDHASH 1_stale"
    assert headers["x-goog-authuser"] == "2"


def test_curl_ansi_c_quoted_cookie():
    text = (
        "curl 'https://music.youtube.com/youtubei/v1/browse' "
        "-H 'accept: */*' "
        f"-H $'cookie: {COOKIE}' "
        "-H 'origin: https://music.youtube.com'"
    )
    headers = parse_headers(text, now=NOW)

    assert headers["cookie"] == COOKIE
    assert headers["authorization"] == _expected_hash()


def test_raw_http_request_line_is_skipped():
    headers = parse_headers(
        f"""POST /youtubei/v1/browse?prettyPrint=false HTTP/1.1
host: music.youtube.com
cookie: {COOKIE}
origin: https://music.youtube.com
""",
        now=NOW,
    )

    assert headers["cookie"] == COOKIE
    assert headers["authorization"] == _expected_hash()


def test_missing_sapisid_raises():
    with pytest.raises(HeaderError, match="__Secure-3PAPISID"):
        parse_headers("cookie: SOCS=CAI\norigin: https://music.youtube.com")


def test_no_auth_headers_raises():
    with pytest.raises(HeaderError, match="No cookie or authorization"):
        parse_headers("accept: */*\nuser-agent: test")


def test_sapisid_hash_is_stable():
    assert sapisid_hash("abc123", "https://music.youtube.com", now=NOW) == _expected_hash()


def test_triple_authorization_is_preserved():
    triple = (
        "SAPISIDHASH 1_a_u SAPISID1PHASH 1_b_u SAPISID3PHASH 1_c_u"
    )
    headers = parse_headers(
        f"authorization: {triple}\ncookie: {COOKIE}\n"
        "origin: https://music.youtube.com\n",
        now=NOW,
    )
    assert headers["authorization"] == triple
