from __future__ import annotations

from spyt.match import (
    DEFAULT_MIN_SCORE,
    artist_similarity,
    duration_similarity,
    match_track,
    normalize_artist,
    normalize_title,
    score_candidate,
    skip_reason,
    title_similarity,
    variant_penalty,
)
from spyt.spotify.models import Album
from spyt.spotify.models import Artist as SpotifyArtist
from spyt.spotify.models import Track as SpotifyTrack
from spyt.youtube.models import Artist as YouTubeArtist
from spyt.youtube.models import SearchTrack


def spotify_track(
    *,
    name: str = "Paradise",
    artist: str = "Lil Uzi Vert",
    duration_ms: int | None = 172703,
    kind: str = "track",
    local: bool = False,
    playable: bool = True,
    position: int = 1,
) -> SpotifyTrack:
    return SpotifyTrack(
        position=position,
        uri="spotify:track:track1",
        id="track1",
        name=name,
        kind=kind,
        artists=[SpotifyArtist(name=artist)],
        album=Album(name="Luv Is Rage"),
        duration_ms=duration_ms,
        local=local,
        playable=playable,
    )


def youtube_track(
    *,
    video_id: str = "video1",
    title: str = "Paradise",
    artist: str = "Lil Uzi Vert",
    duration_seconds: int | None = 173,
    kind: str = "song",
    available: bool = True,
) -> SearchTrack:
    return SearchTrack(
        video_id=video_id,
        title=title,
        kind=kind,
        artists=[YouTubeArtist(name=artist)] if artist else [],
        album=None,
        duration_seconds=duration_seconds,
        available=available,
    )


def test_exact_match_scores_high():
    score = score_candidate(spotify_track(), youtube_track())
    assert score > 0.9


def test_wrong_artist_falls_below_default_threshold():
    score = score_candidate(
        spotify_track(),
        youtube_track(artist="Some Cover Band"),
    )
    assert score < DEFAULT_MIN_SCORE


def test_cover_version_is_penalised_below_threshold():
    score = score_candidate(
        spotify_track(),
        youtube_track(title="Paradise (Cover)"),
    )
    assert score < DEFAULT_MIN_SCORE


def test_karaoke_version_is_penalised_below_threshold():
    score = score_candidate(
        spotify_track(),
        youtube_track(title="Paradise (Karaoke Version)"),
    )
    assert score < DEFAULT_MIN_SCORE


def test_duration_mismatch_rejects_candidate():
    score = score_candidate(
        spotify_track(),
        youtube_track(duration_seconds=173 + 60),
    )
    assert score == 0.0
    assert duration_similarity(172703, 173 + 60) == (0.0, False)


def test_missing_duration_is_neutral():
    score, ok = duration_similarity(172703, None)
    assert ok is True
    assert score == 0.5


def test_title_normalisation_removes_features_and_remasters():
    assert normalize_title("Paradise (feat. Someone)") == "paradise"
    assert normalize_title("Paradise - 2011 Remaster") == "paradise"
    assert title_similarity("Paradise - Remastered", "Paradise") == 1.0


def test_artist_normalisation_removes_featured_clause():
    assert normalize_artist("Lil Uzi Vert feat. Nicki Minaj") == "lil uzi vert"
    assert artist_similarity(["Lil Uzi Vert"], ["Lil Uzi Vert feat. X"]) == 1.0


def test_variant_penalty_ignores_keyword_present_in_source():
    assert variant_penalty("Live Forever", "Live Forever") == 0.0


def test_match_track_uses_best_song_result_and_stops():
    calls = []
    candidates = [
        youtube_track(video_id="bad", artist="Cover Band"),
        youtube_track(video_id="good"),
    ]

    def search(query, *, filter="songs", limit=8):
        calls.append((query, filter))
        return candidates

    result = match_track(spotify_track(), search)
    assert result.status == "matched"
    assert result.youtube is not None
    assert result.youtube.video_id == "good"
    assert [call[1] for call in calls] == ["songs"]


def test_match_track_falls_back_to_videos():
    calls = []

    def search(query, *, filter="songs", limit=8):
        calls.append((query, filter))
        if filter == "songs":
            return []
        return [youtube_track(kind="video")]

    result = match_track(spotify_track(), search)
    assert result.status == "matched"
    assert [call[1] for call in calls] == ["songs", "videos"]


def test_match_track_keeps_low_confidence_candidate_for_review():
    def search(query, *, filter="songs", limit=8):
        return [youtube_track(artist="Someone Else")]

    result = match_track(spotify_track(), search)
    assert result.status == "unmatched"
    assert result.youtube is not None
    assert result.score < DEFAULT_MIN_SCORE
    assert "below" in (result.reason or "")


def test_match_track_reports_no_results():
    def search(query, *, filter="songs", limit=8):
        return []

    result = match_track(spotify_track(), search)
    assert result.status == "unmatched"
    assert result.youtube is None
    assert result.reason == "no results"


def test_local_unplayable_and_episode_tracks_are_skipped():
    assert skip_reason(spotify_track(local=True)) == "local file"
    assert skip_reason(spotify_track(playable=False)) == "unavailable"
    assert skip_reason(spotify_track(kind="episode")) == "podcast episode"
    assert skip_reason(spotify_track()) is None

    def search(query, *, filter="songs", limit=8):
        raise AssertionError("search should not be called for skipped tracks")

    result = match_track(spotify_track(local=True), search)
    assert result.status == "skipped"
    assert result.youtube is None
