import hashlib

from spyt.youtube.auth import build_authorization, parse_browser_session

COOKIE = "SAPISID=s1; __Secure-1PAPISID=p1; __Secure-3PAPISID=p3"
ORIGIN = "https://music.youtube.com"
NOW = 1_700_000_000


def _digest(payload: str) -> str:
    return hashlib.sha1(payload.encode()).hexdigest()


def test_build_authorization_with_user_session_id():
    authorization = build_authorization(
        COOKIE, ORIGIN, user_session_id="USER123", now=NOW
    )
    assert authorization is not None
    tokens = authorization.split()

    assert tokens[0::2] == ["SAPISIDHASH", "SAPISID1PHASH", "SAPISID3PHASH"]
    assert tokens[1] == (
        f"1700000000_{_digest('USER123 1700000000 s1 https://music.youtube.com')}_u"
    )
    assert tokens[3] == (
        f"1700000000_{_digest('USER123 1700000000 p1 https://music.youtube.com')}_u"
    )
    assert tokens[5] == (
        f"1700000000_{_digest('USER123 1700000000 p3 https://music.youtube.com')}_u"
    )


def test_build_authorization_without_user_session_id():
    authorization = build_authorization(COOKIE, ORIGIN, now=NOW)
    assert authorization is not None
    tokens = authorization.split()
    assert tokens[1] == f"1700000000_{_digest('1700000000 s1 https://music.youtube.com')}"
    assert not any(token.endswith("_u") for token in tokens)


def test_build_authorization_falls_back_to_3p_sapisid():
    authorization = build_authorization(
        "__Secure-3PAPISID=p3", ORIGIN, now=NOW
    )
    assert authorization is not None
    tokens = authorization.split()
    assert tokens[0] == "SAPISIDHASH"
    assert tokens[2] == "SAPISID3PHASH"


def test_parse_primary_account_session():
    html = (
        '<script>ytcfg.set({"LOGGED_IN": true, "DATASYNC_ID": "USER123||", '
        '"SESSION_INDEX": 2});</script>'
    )
    session = parse_browser_session(html)

    assert session.logged_in is True
    assert session.user_session_id == "USER123"
    assert session.delegated_session_id is None
    assert session.session_index == 2


def test_parse_brand_account_session():
    html = (
        '<script>ytcfg.set({"LOGGED_IN": true, '
        '"DATASYNC_ID": "DELEGATED||USER456"});</script>'
    )
    session = parse_browser_session(html)

    assert session.logged_in is True
    assert session.user_session_id == "USER456"
    assert session.delegated_session_id == "DELEGATED"


def test_parse_logged_out_session():
    html = (
        '<script>ytcfg.set({"LOGGED_IN": false, '
        '"DATASYNC_ID": "DELEGATED||"});</script>'
    )
    session = parse_browser_session(html)

    assert session.logged_in is False
    assert session.user_session_id is None


def test_parse_prefers_logged_in_config_block():
    html = (
        '<script>ytcfg.set({"LOGGED_IN": false});</script>'
        '<script>ytcfg.set({"LOGGED_IN": true, "DATASYNC_ID": "USER789||"});</script>'
    )
    session = parse_browser_session(html)

    assert session.logged_in is True
    assert session.user_session_id == "USER789"


def test_parse_missing_config():
    session = parse_browser_session("<html></html>")
    assert session.logged_in is False
