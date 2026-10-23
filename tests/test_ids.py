import pytest

from spyt.spotify.ids import InvalidPlaylistReference, parse_playlist_id

PLAYLIST_ID = "6yNRwWCVVNLEsif1esJ1Cc"


@pytest.mark.parametrize(
    "reference",
    [
        PLAYLIST_ID,
        f"spotify:playlist:{PLAYLIST_ID}",
        f"https://open.spotify.com/playlist/{PLAYLIST_ID}",
        f"https://open.spotify.com/intl-it/playlist/{PLAYLIST_ID}?si=abc123",
        f"  https://open.spotify.com/playlist/{PLAYLIST_ID}  ",
    ],
)
def test_playlist_references(reference):
    assert parse_playlist_id(reference) == PLAYLIST_ID


@pytest.mark.parametrize(
    "reference",
    ["", "not a playlist", "spotify:album:abc", "https://open.spotify.com/album/abc"],
)
def test_invalid_references(reference):
    with pytest.raises(InvalidPlaylistReference):
        parse_playlist_id(reference)
