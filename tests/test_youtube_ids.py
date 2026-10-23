import pytest

from spyt.youtube.ids import InvalidPlaylistReference, parse_playlist_id

PLAYLIST_ID = "PLQwVIlKxHM6qv-o99iX9R85og7IzF9YS_"


@pytest.mark.parametrize(
    "reference",
    [
        PLAYLIST_ID,
        f"VL{PLAYLIST_ID}",
        f"https://music.youtube.com/playlist?list={PLAYLIST_ID}",
        f"https://music.youtube.com/watch?v=ekz1IJ9I0sw&list={PLAYLIST_ID}",
        f"https://www.youtube.com/playlist?list={PLAYLIST_ID}&si=abc",
        f"music.youtube.com/playlist?list={PLAYLIST_ID}",
        f"https://music.youtube.com/browse/VL{PLAYLIST_ID}",
        "LM",
    ],
)
def test_playlist_references(reference):
    expected = "LM" if reference == "LM" else PLAYLIST_ID
    assert parse_playlist_id(reference) == expected


def test_vl_prefix_is_removed():
    assert parse_playlist_id("VLPLabc123") == "PLabc123"


@pytest.mark.parametrize(
    "reference",
    [
        "",
        "not a playlist",
        "https://music.youtube.com/watch?v=ekz1IJ9I0sw",
        "https://music.youtube.com/album/MPREb_x",
    ],
)
def test_invalid_references(reference):
    with pytest.raises(InvalidPlaylistReference):
        parse_playlist_id(reference)
