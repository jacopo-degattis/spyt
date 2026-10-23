"""Match Spotify tracks to YouTube Music search results.

The matcher combines three signals — title, artist and duration — and rejects
obvious traps such as covers, karaoke versions, sped-up edits and live versions
when the Spotify track is the studio recording.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher

from .spotify.models import Track as SpotifyTrack
from .youtube.models import SearchTrack

DEFAULT_MIN_SCORE = 0.80
DEFAULT_SEARCH_LIMIT = 8

SearchFunction = Callable[..., list[SearchTrack]]

_FEAT_INLINE_RE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+.*$", re.IGNORECASE)
_BRACKET_RE = re.compile(r"[\(\[]([^)\]]*)[\)\]]")
_DASH_VERSION_RE = re.compile(
    r"\s+-\s+[^-]*\b(?:remaster(?:ed)?|version|edit|deluxe|bonus|explicit|clean)\b.*$",
    re.IGNORECASE,
)
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")

# Keywords that indicate a different recording than the Spotify track.
# Each group is penalised once; the penalty is skipped when the Spotify title
# contains the keyword too (e.g. the song itself is called "Live Forever").
_VARIANT_GROUPS: tuple[tuple[str, ...], ...] = (
    ("cover", "covers", "cover version"),
    ("karaoke", "backing track"),
    ("instrumental", "instrumentals"),
    ("sped up", "speed up", "slowed", "nightcore", "8d audio", "reverb"),
    ("live", "concert", "session"),
    ("remix", "remixed", "mashup", "bootleg"),
    ("acoustic", "piano version"),
)
_VARIANT_PENALTY = 0.25
_MAX_VARIANT_PENALTY = 0.40


@dataclass
class MatchResult:
    """Outcome of matching one Spotify track against YouTube Music."""

    spotify: SpotifyTrack
    youtube: SearchTrack | None
    score: float
    status: str  # "matched", "unmatched" or "skipped"
    reason: str | None = None
    query: str | None = None


def skip_reason(track: SpotifyTrack) -> str | None:
    """Return why a Spotify track cannot be transferred, if it cannot."""
    if track.local:
        return "local file"
    if not track.playable:
        return "unavailable"
    if track.kind == "episode":
        return "podcast episode"
    return None


def match_track(
    track: SpotifyTrack,
    search: SearchFunction,
    *,
    min_score: float = DEFAULT_MIN_SCORE,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> MatchResult:
    """Find the best YouTube Music result for ``track``.

    ``search`` is called as ``search(query, filter=..., limit=...)`` and must
    return :class:`SearchTrack` objects. Looking it up as a parameter keeps the
    scoring testable without network access.
    """
    reason = skip_reason(track)
    if reason is not None:
        return MatchResult(
            spotify=track,
            youtube=None,
            score=0.0,
            status="skipped",
            reason=reason,
        )

    queries = list(_search_queries(track))
    seen: set[str] = set()
    best: tuple[float, SearchTrack, str] | None = None

    for filter_name in ("songs", "videos"):
        for query in queries:
            results = search(query, filter=filter_name, limit=limit)
            for candidate in results:
                if candidate.video_id in seen:
                    continue
                seen.add(candidate.video_id)
                score = score_candidate(track, candidate)
                if score <= 0.0:
                    continue
                if best is None or score > best[0]:
                    best = (score, candidate, query)

        # Stop as soon as the song catalogue produced a confident match;
        # falling back to "videos" is only for tracks without an album upload.
        if best is not None and best[0] >= min_score:
            break

    if best is None:
        return MatchResult(
            spotify=track,
            youtube=None,
            score=0.0,
            status="unmatched",
            reason="no results",
            query=queries[0] if queries else None,
        )

    score, candidate, query = best
    if score < min_score:
        return MatchResult(
            spotify=track,
            youtube=candidate,
            score=score,
            status="unmatched",
            reason=f"best score {score:.2f} is below {min_score:.2f}",
            query=query,
        )

    return MatchResult(
        spotify=track,
        youtube=candidate,
        score=score,
        status="matched",
        query=query,
    )


def score_candidate(track: SpotifyTrack, candidate: SearchTrack) -> float:
    """Score a search result against a Spotify track from 0 (reject) to 1."""
    title_score = title_similarity(track.name, candidate.title)
    if title_score < 0.4:
        return 0.0

    artist_score = artist_similarity(
        [artist.name for artist in track.artists],
        [artist.name for artist in candidate.artists],
    )
    duration_score, duration_ok = duration_similarity(
        track.duration_ms,
        candidate.duration_seconds,
    )
    if not duration_ok:
        return 0.0

    score = 0.50 * title_score + 0.35 * artist_score + 0.15 * duration_score

    if candidate.kind == "song":
        score += 0.03
    elif candidate.kind == "video":
        score -= 0.01

    if not candidate.available:
        score -= 0.25

    score -= variant_penalty(track.name, candidate.title)
    return max(0.0, min(1.0, score))


def title_similarity(source: str, candidate: str) -> float:
    """Similarity of two song titles after removing version/feature noise."""
    left = normalize_title(source)
    right = normalize_title(candidate)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        return 0.95

    ratio = SequenceMatcher(None, left, right).ratio()
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    union = left_tokens | right_tokens
    jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
    return max(ratio, 0.5 * ratio + 0.5 * jaccard)


def artist_similarity(source_names: list[str], candidate_names: list[str]) -> float:
    """Best pairwise artist-name similarity."""
    if not source_names:
        return 0.5
    if not candidate_names:
        return 0.4

    best = 0.0
    for source in source_names:
        left = normalize_artist(source)
        if not left:
            continue
        for candidate in candidate_names:
            right = normalize_artist(candidate)
            if not right:
                continue
            if left == right:
                return 1.0
            if left in right or right in left:
                score = 0.9
            else:
                score = SequenceMatcher(None, left, right).ratio()
            best = max(best, score)
    return best


def duration_similarity(
    source_ms: int | None,
    candidate_seconds: int | None,
) -> tuple[float, bool]:
    """Return ``(score, acceptable)`` for two durations.

    A duration gap larger than roughly 12% (and at least 12 seconds) means the
    result is probably a different recording, so it is rejected outright.
    """
    if source_ms is None or candidate_seconds is None:
        return 0.5, True

    source_seconds = source_ms / 1000
    difference = abs(source_seconds - candidate_seconds)
    tolerance = max(12.0, min(30.0, source_seconds * 0.12))
    if difference > tolerance:
        return 0.0, False
    return 1.0 - 0.5 * (difference / tolerance), True


def variant_penalty(source_title: str, candidate_title: str) -> float:
    """Penalise covers, live versions and similar variants of the same song."""
    source = normalize_text(source_title)
    candidate = normalize_text(candidate_title)

    penalty = 0.0
    for group in _VARIANT_GROUPS:
        if not any(keyword in candidate for keyword in group):
            continue
        if any(keyword in source for keyword in group):
            continue
        penalty += _VARIANT_PENALTY
    return min(penalty, _MAX_VARIANT_PENALTY)


def normalize_title(text: str) -> str:
    """Lowercase a title and strip featuring/version qualifiers."""
    text = _BRACKET_RE.sub(_strip_bracket_contents, text)
    text = _DASH_VERSION_RE.sub("", text)
    return normalize_text(text)


def normalize_artist(text: str) -> str:
    """Lowercase an artist name and drop a trailing featured-artist clause."""
    return normalize_text(_FEAT_INLINE_RE.sub("", text))


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower().replace("&", " and ")
    text = _NON_WORD_RE.sub(" ", text)
    return " ".join(text.split())


def _strip_bracket_contents(match: re.Match[str]) -> str:
    contents = match.group(1)
    if re.search(
        r"feat|ft\.?|featuring|with|remaster|version|edit|deluxe|bonus|explicit|clean",
        contents,
        re.IGNORECASE,
    ):
        return " "
    return match.group(0)


def _search_queries(track: SpotifyTrack) -> list[str]:
    title = _clean_query_title(track.name)
    artists = [artist.name for artist in track.artists if artist.name]

    queries: list[str] = []
    if artists:
        queries.append(f"{title} {artists[0]}")
        if len(artists) > 1:
            combined = f"{title} {' '.join(artists)}"
            if combined not in queries:
                queries.append(combined)
    else:
        queries.append(title)
    return queries


def _clean_query_title(title: str) -> str:
    """Drop Spotify's version suffixes so the search query stays focused."""
    cleaned = _BRACKET_RE.sub(_strip_bracket_contents, title)
    cleaned = _DASH_VERSION_RE.sub("", cleaned)
    return " ".join(cleaned.split()) or title
