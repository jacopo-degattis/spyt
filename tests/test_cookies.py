import json

import pytest

from spyt.errors import CookieError
from spyt.spotify.cookies import parse_cookies

NETSCAPE = """# Netscape HTTP Cookie File
# https://curl.se/docs/http-cookies.html
.spotify.com\tTRUE\t/\tTRUE\t1893456000\tsp_dc\tAQC-test-dc
#HttpOnly_.spotify.com\tTRUE\t/\tTRUE\t1893456000\tsp_key\ttest-key
.example.com\tTRUE\t/\tFALSE\t1893456000\tother\tignored
"""


def test_netscape_cookies_txt():
    cookies = parse_cookies(NETSCAPE)
    assert cookies == {"sp_dc": "AQC-test-dc", "sp_key": "test-key"}


def test_cookie_editor_json_filters_other_domains():
    text = json.dumps(
        [
            {"name": "sp_dc", "value": "dc-value", "domain": ".spotify.com"},
            {"name": "sp_key", "value": "key-value", "domain": "open.spotify.com"},
            {"name": "session", "value": "nope", "domain": ".example.com"},
        ]
    )
    assert parse_cookies(text) == {"sp_dc": "dc-value", "sp_key": "key-value"}


def test_playwright_storage_state():
    text = json.dumps(
        {
            "cookies": [
                {
                    "name": "sp_dc",
                    "value": "playwright-dc",
                    "domain": ".spotify.com",
                    "path": "/",
                    "expires": -1,
                }
            ],
            "origins": [],
        }
    )
    assert parse_cookies(text) == {"sp_dc": "playwright-dc"}


def test_plain_name_value_json():
    assert parse_cookies('{"sp_dc": "mapping-dc"}') == {"sp_dc": "mapping-dc"}


def test_cookie_header():
    cookies = parse_cookies("sp_dc=header-dc; sp_key=header-key")
    assert cookies == {"sp_dc": "header-dc", "sp_key": "header-key"}


def test_bare_sp_dc_value():
    value = "A" * 60
    assert parse_cookies(value) == {"sp_dc": value}


def test_invalid_input_raises():
    with pytest.raises(CookieError):
        parse_cookies("not a cookie file at all")
