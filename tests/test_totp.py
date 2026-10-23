import base64

import pytest

from spoyt.errors import AuthError
from spoyt.spotify.totp import (
    candidate_secrets,
    derive_secret,
    generate_totp,
    latest_secret,
)


def test_rfc6238_sha1_vector():
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert generate_totp(secret, timestamp=59, digits=8) == "94287082"
    assert generate_totp(secret, timestamp=59, digits=6) == "287082"


def test_derive_secret_round_trip():
    secret = derive_secret([1, 2, 3])
    padding = "=" * (-len(secret) % 8)
    assert base64.b32decode(secret + padding) == b"888"


def test_candidate_secrets_are_sorted_newest_first():
    secrets = {"1": [1], "9": [9], "3": [3]}
    assert [version for version, _ in candidate_secrets(secrets, limit=2)] == ["9", "3"]
    assert latest_secret(secrets)[0] == "9"


def test_candidate_secrets_skips_empty_ciphers():
    with pytest.raises(AuthError):
        candidate_secrets({"1": []})
