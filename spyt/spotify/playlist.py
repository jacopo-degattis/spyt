"""Convert pathfinder ``fetchPlaylist`` payloads into model objects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..errors import PlaylistNotFoundError, SpotifyAPIError
from .models import Album, Artist, Owner, Playlist, SkippedItem, Track


@dataclass
class PlaylistPage:
    playlist: Playlist
    items: list[Track | SkippedItem]
    total_items: int


def parse_playlist_page(
    payload: dict[str, Any],
    *,
    playlist_id: str,
    offset: int = 0,
) -> PlaylistPage:
    playlist_data = _extract_playlist(payload, playlist_id)
    content = playlist_data.get("content") or {}
    raw_items = content.get("items") or []
    if not isinstance(raw_items, list):
        raise SpotifyAPIError("Unexpected playlist content in Spotify response")

    total_items = _as_int(content.get("totalCount"))
    if total_items is None:
        total_items = offset + len(raw_items)

    items: list[Track | SkippedItem] = []
    for index, raw_item in enumerate(raw_items):
        position = offset + index + 1
        if not isinstance(raw_item, dict):
            items.append(SkippedItem(position=position, reason="unknown"))
            continue
        parsed = _parse_item(raw_item, position)
        if parsed is None:
            items.append(SkippedItem(position=position, reason="unavailable"))
        else:
            items.append(parsed)

    return PlaylistPage(
        playlist=_parse_header(playlist_data, playlist_id=playlist_id, total_items=total_items),
        items=items,
        total_items=total_items,
    )


def _extract_playlist(payload: dict[str, Any], playlist_id: str) -> dict[str, Any]:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise SpotifyAPIError("Spotify returned no data for this playlist", payload=payload)

    playlist = data.get("playlistV2")
    if playlist is None:
        playlist = data.get("playlist")
    if not isinstance(playlist, dict):
        raise PlaylistNotFoundError(playlist_id)

    typename = playlist.get("__typename")
    message = playlist.get("message")
    if typename == "NotFound" or "not found" in str(message or "").lower():
        raise PlaylistNotFoundError(playlist_id, message)
    if typename in {"GenericError", "Error", "GenericErrorResponse"}:
        raise SpotifyAPIError(
            message or "Spotify could not load this playlist", payload=playlist
        )

    return playlist


def _parse_header(
    playlist: dict[str, Any],
    *,
    playlist_id: str,
    total_items: int,
) -> Playlist:
    uri = playlist.get("uri") or f"spotify:playlist:{playlist_id}"
    if isinstance(uri, str) and uri.startswith("spotify:playlist:"):
        playlist_id = uri.rsplit(":", 1)[-1]

    images = playlist.get("images") or {}
    image_items = images.get("items") if isinstance(images, dict) else None
    cover_url = None
    if isinstance(image_items, list):
        for image in image_items:
            if isinstance(image, dict):
                cover_url = _best_source(image.get("sources"))
                if cover_url:
                    break
    if not cover_url:
        visual = playlist.get("visualIdentity") or {}
        square = visual.get("squareCoverImage") or {}
        cover_url = _best_source(square.get("sources"))

    return Playlist(
        id=playlist_id,
        uri=uri,
        name=playlist.get("name") or "",
        description=playlist.get("description") or "",
        owner=_parse_owner(playlist),
        cover_url=cover_url,
        total_items=total_items,
        revision_id=playlist.get("revisionId"),
    )


def _parse_owner(playlist: dict[str, Any]) -> Owner | None:
    owner = playlist.get("ownerV2") or playlist.get("owner")
    if isinstance(owner, dict) and isinstance(owner.get("data"), dict):
        owner = owner["data"]
    if isinstance(owner, dict):
        name = owner.get("name") or owner.get("displayName")
        username = owner.get("username") or owner.get("id")
        uri = owner.get("uri")
        if name or username or uri:
            return Owner(name=name, uri=uri, username=username)

    members = playlist.get("members") or {}
    for member in members.get("items") or []:
        if isinstance(member, dict) and member.get("isOwner"):
            user = member.get("user") or {}
            if isinstance(user.get("data"), dict):
                user = user["data"]
            if isinstance(user, dict):
                return Owner(
                    name=user.get("name"),
                    uri=user.get("uri"),
                    username=user.get("username"),
                )
    return None


def _parse_item(item: dict[str, Any], position: int) -> Track | SkippedItem | None:
    wrapper = item.get("itemV2") or {}
    data = wrapper.get("data") if isinstance(wrapper, dict) else None
    if isinstance(data, dict) and data:
        return _from_item_v2(data, item, position)

    legacy_track = item.get("track")
    if isinstance(legacy_track, dict) and legacy_track:
        return _from_item_v2(legacy_track, item, position)

    entity = _entity_v3(item)
    if entity:
        return _from_item_v3(entity, item, position)

    return None


def _from_item_v2(data: dict[str, Any], item: dict[str, Any], position: int) -> Track:
    typename = str(data.get("__typename") or "").lower()
    kind = _kind_from_typename(typename)
    uri = data.get("uri")
    name = data.get("name") or ""

    artists: list[Artist] = []
    artists_block = data.get("artists") or {}
    for entry in artists_block.get("items") or []:
        if isinstance(entry, dict):
            profile = entry.get("profile") or {}
            artist_name = profile.get("name") or entry.get("name")
            if artist_name:
                artists.append(Artist(name=artist_name, uri=entry.get("uri")))

    album = _parse_album_v2(data, kind)
    duration = (
        _milliseconds(data.get("trackDuration"))
        or _milliseconds(data.get("duration"))
        or _milliseconds(data.get("durationMs"))
    )
    explicit = _content_rating_is_explicit(data.get("contentRating"))
    if not explicit and isinstance(data.get("explicit"), bool):
        explicit = data["explicit"]

    playability = data.get("playability") or {}
    playable = playability.get("playable")
    if not isinstance(playable, bool):
        playable = bool(data.get("isPlayable", True))

    return Track(
        position=position,
        uri=uri if isinstance(uri, str) else None,
        id=_id_from_uri(uri, kind),
        name=name,
        kind=kind,
        artists=artists,
        album=album,
        duration_ms=duration,
        explicit=explicit,
        playable=playable,
        local=_is_local(uri, data),
        added_at=_iso(item.get("addedAt")),
        added_by=_added_by(item),
        playcount=_as_int(data.get("playcount")),
    )


def _parse_album_v2(data: dict[str, Any], kind: str) -> Album | None:
    if kind == "episode":
        show = data.get("podcastV2") or data.get("show") or {}
        if isinstance(show.get("data"), dict):
            show = show["data"]
        if isinstance(show, dict) and (show.get("name") or show.get("uri")):
            return Album(
                name=show.get("name"),
                uri=show.get("uri"),
                cover_url=_best_source((show.get("coverArt") or {}).get("sources")),
                release_date=_iso(show.get("releaseDate")),
            )
        return None

    album = data.get("albumOfTrack")
    if not isinstance(album, dict):
        return None
    return Album(
        name=album.get("name"),
        uri=album.get("uri"),
        cover_url=_best_source((album.get("coverArt") or {}).get("sources")),
        release_date=_iso(album.get("date")),
    )


def _entity_v3(item: dict[str, Any]) -> dict[str, Any]:
    wrapper = item.get("itemV3") or {}
    entity = wrapper.get("data") if isinstance(wrapper, dict) else None
    return entity if isinstance(entity, dict) else {}


def _from_item_v3(entity: dict[str, Any], item: dict[str, Any], position: int) -> Track:
    identity = entity.get("identityTrait") or {}
    entity_type = str((entity.get("entityTypeTrait") or {}).get("type") or "")
    kind = _kind_from_typename(entity_type)

    artists: list[Artist] = []
    for contributor in (identity.get("contributors") or {}).get("items") or []:
        if isinstance(contributor, dict) and contributor.get("name"):
            artists.append(Artist(name=contributor["name"], uri=contributor.get("uri")))

    parent = identity.get("contentHierarchyParent") or {}
    parent_identity = parent.get("identityTrait") or {}
    album = None
    if parent_identity.get("name") or parent.get("uri"):
        publishing = parent.get("publishingMetadataTrait") or {}
        visual = (entity.get("visualIdentityTrait") or {})
        album = Album(
            name=parent_identity.get("name"),
            uri=parent.get("uri"),
            cover_url=_best_source(
                (visual.get("squareCoverImage") or {}).get("sources")
            ),
            release_date=_iso(publishing.get("firstPublishedAt")),
        )

    duration = None
    consumption = entity.get("consumptionExperienceTrait") or {}
    seconds = (consumption.get("duration") or {}).get("seconds")
    if isinstance(seconds, (int, float)):
        duration = int(seconds * 1000)

    uri = entity.get("uri") or identity.get("uri")
    playability = entity.get("playability") or {}

    return Track(
        position=position,
        uri=uri if isinstance(uri, str) else None,
        id=_id_from_uri(uri, kind),
        name=identity.get("name") or "",
        kind=kind,
        artists=artists,
        album=album,
        duration_ms=duration,
        explicit=_has_explicit_rating(consumption.get("contentRatings")),
        playable=bool(playability.get("playable", True)),
        local=_is_local(uri, entity),
        added_at=_iso(item.get("addedAt")),
        added_by=_added_by(item),
    )


def _kind_from_typename(typename: str) -> str:
    lowered = typename.lower()
    if "episode" in lowered or "podcast" in lowered:
        return "episode"
    if "track" in lowered or "song" in lowered:
        return "track"
    if "local" in lowered:
        return "track"
    return "unknown"


def _id_from_uri(uri: Any, kind: str) -> str | None:
    if not isinstance(uri, str) or not uri.startswith("spotify:"):
        return None
    parts = uri.split(":")
    if len(parts) < 3:
        return None
    if kind == "episode" and parts[1] == "episode":
        return parts[2]
    if kind == "track" and parts[1] == "track":
        return parts[2]
    return parts[2]


def _is_local(uri: Any, data: dict[str, Any]) -> bool:
    if isinstance(uri, str) and uri.startswith("spotify:local:"):
        return True
    return bool(data.get("isLocal") or data.get("is_local"))


def _best_source(sources: Any) -> str | None:
    if not isinstance(sources, list):
        return None

    def area(source: dict[str, Any]) -> int:
        width = source.get("width") or 0
        height = source.get("height") or 0
        if not isinstance(width, int) or not isinstance(height, int):
            return 0
        return width * height

    valid = [source for source in sources if isinstance(source, dict) and source.get("url")]
    if not valid:
        return None
    return max(valid, key=area).get("url")


def _milliseconds(value: Any) -> int | None:
    if not isinstance(value, dict):
        return None
    milliseconds = value.get("totalMilliseconds", value.get("milliseconds"))
    return _as_int(milliseconds)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _iso(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        iso = value.get("isoString") or value.get("iso8601")
        if isinstance(iso, str):
            return iso
    return None


def _added_by(item: dict[str, Any]) -> str | None:
    added_by = item.get("addedBy") or {}
    user = added_by.get("data") if isinstance(added_by, dict) else None
    if not isinstance(user, dict):
        return None
    return user.get("name") or user.get("username")


def _content_rating_is_explicit(content_rating: Any) -> bool:
    if not isinstance(content_rating, dict):
        return False
    label = content_rating.get("label") or content_rating.get("tag")
    return str(label or "").lower() == "explicit"


def _has_explicit_rating(ratings: Any) -> bool:
    if not isinstance(ratings, list):
        return False
    for rating in ratings:
        if isinstance(rating, str) and rating.lower() == "explicit":
            return True
        if isinstance(rating, dict):
            label = rating.get("label") or rating.get("tag") or rating.get("rating")
            if str(label or "").lower() == "explicit":
                return True
    return False
