"""Read public YouTube Music metadata without any download dependencies.

The API client is created on first use. Tests and embedded callers can supply an
object implementing the corresponding ytmusicapi methods instead.
"""
import logging
import re
import shutil
import subprocess
import sys
from urllib.parse import parse_qs, urlparse


logger = logging.getLogger(__name__)
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
COLLECTION_ID = re.compile(r"(?:PL|RD|UU|LL|LM|OLAK5uy_|MPRE)[A-Za-z0-9_-]+\Z")
HOSTS = {"youtube.com", "www.youtube.com", "music.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}


class SourceError(ValueError):
    """A metadata source or clipboard could not be read."""


def reference(value: str) -> tuple[str, str]:
    """Return (song|playlist|album, id), accepting only YouTube references.

    A watch URL containing ``list=`` intentionally means the whole collection,
    matching links copied from the Music playlist and album interfaces.
    """
    value = value.strip()
    if VIDEO_ID.fullmatch(value):
        return "song", value
    if COLLECTION_ID.fullmatch(value):
        return ("album" if value.startswith("MPRE") else "playlist"), value
    if "://" not in value and value.split("/", 1)[0].lower() in HOSTS:
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in ("https", "http") or parsed.hostname not in HOSTS or parsed.username or parsed.password:
        raise SourceError("use a YouTube video, playlist or album URL, or search for a song")
    query = parse_qs(parsed.query)
    collection = (query.get("list") or [None])[0]
    if collection and re.fullmatch(r"[A-Za-z0-9_-]+", collection):
        return "playlist", collection
    parts = parsed.path.strip("/").split("/")
    if len(parts) == 2 and parts[0] == "browse" and re.fullmatch(r"MPRE[A-Za-z0-9_-]+", parts[1]):
        return "album", parts[1]
    video_id = None
    if parsed.hostname in {"youtu.be", "www.youtu.be"} and len(parts) == 1:
        video_id = parts[0]
    elif parts == ["watch"]:
        video_id = (query.get("v") or [None])[0]
    elif len(parts) == 2 and parts[0] in {"shorts", "embed", "live"}:
        video_id = parts[1]
    if video_id and VIDEO_ID.fullmatch(video_id):
        return "song", video_id
    raise SourceError("the YouTube URL does not contain a valid video, playlist or album ID")


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _positive_int(value) -> int | None:
    try:
        number = int(value)
        return number if number > 0 else None
    except (ValueError, TypeError):
        return None


def _cover(item: dict) -> str | None:
    thumbs = item.get("thumbnails") or item.get("thumbnail") or []
    if isinstance(thumbs, dict):
        thumbs = thumbs.get("thumbnails", [])
    if not isinstance(thumbs, list):
        return None
    candidates = [t for t in thumbs if isinstance(t, dict) and isinstance(t.get("url"), str)
                  and urlparse(t["url"]).scheme in {"https", "http"}]
    if not candidates:
        return None
    best = max(candidates, key=lambda t: (_positive_int(t.get("width")) or 0) * (_positive_int(t.get("height")) or 0))
    return best["url"]


def metadata(item: dict, *, fallback: dict | None = None) -> dict:
    """Normalize a song, search result, or playlist item into shared fields."""
    fallback = fallback or {}
    fields = dict(fallback)
    title = _text(item.get("title"))
    if title:
        fields["title"] = title
    artists = item.get("artists")
    if isinstance(artists, list):
        names = [_text(a.get("name")) if isinstance(a, dict) else _text(a) for a in artists]
        if any(names):
            fields["artists"] = list(dict.fromkeys(n for n in names if n))
    elif artist := _text(item.get("artist")) or _text(item.get("author")):
        fields["artists"] = [artist.removesuffix(" - Topic")]
    album = item.get("album")
    if isinstance(album, dict):
        album = album.get("name") or album.get("title")
    if _text(album):
        fields["album"] = _text(album)
    for key, value in (("year", item.get("year")), ("track", item.get("trackNumber") or item.get("track"))):
        number = _positive_int(value)
        if number:
            fields[key] = number
    if cover := _cover(item):
        fields["cover_url"] = cover
    fields.setdefault("artists", [])
    return fields


class Source:
    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            try:
                from ytmusicapi import YTMusic
            except ImportError as error:
                raise SourceError("metadata requires ytmusicapi; reinstall ytmm with its dependencies") from error
            self._client = YTMusic()
        return self._client

    def _call(self, method: str, *args, **kwargs):
        try:
            return getattr(self.client, method)(*args, **kwargs)
        except SourceError:
            raise
        except Exception as error:
            raise SourceError(f"YouTube Music {method} failed: {error}") from error

    def song(self, video_id: str) -> dict:
        if not VIDEO_ID.fullmatch(video_id):
            raise SourceError("invalid YouTube video ID")
        fields = {}
        failure = SourceError(f"{video_id}: metadata unavailable")
        try:
            result = self._call("get_song", video_id)
            details = result.get("videoDetails") if isinstance(result, dict) else None
            if isinstance(details, dict) and _text(details.get("title")):
                if details.get("videoId", video_id) != video_id:
                    raise SourceError("YouTube Music returned metadata for a different video")
                fields = metadata(details)
            elif isinstance(result, dict):
                reason = (result.get("playabilityStatus") or {}).get("reason", "metadata unavailable")
                failure = SourceError(f"{video_id}: {reason}")
        except SourceError as error:
            failure = error
        # The player response has the uploader; the watch endpoint provides the
        # actual recording artists and album. It may be absent for ordinary videos.
        try:
            watch = self._call("get_watch_playlist", videoId=video_id, limit=1)
            for track in watch.get("tracks", []):
                if isinstance(track, dict) and track.get("videoId") == video_id:
                    fields = metadata(track, fallback=fields)
                    break
        except (SourceError, AttributeError, TypeError):
            logger.debug("watch metadata unavailable for %s", video_id, exc_info=True)
        if not fields.get("title"):
            raise failure
        return fields

    def playlist(self, value: str) -> list[tuple[str, dict]]:
        kind, collection_id = reference(value)
        if kind == "song":
            raise SourceError("expected a playlist or album URL")
        # An album copied from YouTube Music is commonly an OLAK audio playlist.
        if kind == "playlist" and collection_id.startswith("OLAK5uy_"):
            browse_id = self._call("get_album_browse_id", collection_id)
            if browse_id:
                kind, collection_id = "album", browse_id
        if kind == "album":
            result = self._call("get_album", collection_id)
        else:
            # None retrieves every continuation, rather than silently truncating
            # a long playlist at ytmusicapi's default of 100 tracks.
            result = self._call("get_playlist", collection_id, limit=None)
        if not isinstance(result, dict) or not isinstance(result.get("tracks"), list):
            raise SourceError("YouTube Music returned an invalid playlist")
        fallback = {}
        if kind == "album":
            fallback = metadata(result)
            fallback.pop("title", None)
            if title := _text(result.get("title")):
                fallback["album"] = title
        songs = []
        seen = set()
        for index, item in enumerate(result["tracks"], 1):
            if not isinstance(item, dict) or item.get("isAvailable") is False:
                continue
            video_id = item.get("videoId")
            if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id) or video_id in seen:
                continue
            fields = metadata(item, fallback=fallback)
            if not fields.get("title"):
                continue
            if kind == "album":
                fields.setdefault("track", index)
            seen.add(video_id)
            songs.append((video_id, fields))
        return songs

    def search(self, query: str, limit: int = 20) -> list[tuple[str, dict]]:
        if not query.strip():
            raise SourceError("enter a song or artist to search for")
        if not isinstance(limit, int) or limit < 1:
            raise SourceError("search limit must be positive")
        results = self._call("search", query.strip(), filter="songs", limit=limit)
        if not isinstance(results, list):
            raise SourceError("YouTube Music returned invalid search results")
        songs = []
        seen = set()
        for item in results:
            if not isinstance(item, dict) or item.get("isAvailable") is False:
                continue
            video_id = item.get("videoId")
            if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id) or video_id in seen:
                continue
            fields = metadata(item)
            if not fields.get("title"):
                continue
            songs.append((video_id, fields))
            seen.add(video_id)
        return songs[:limit]

    def resolve(self, value: str) -> list[tuple[str, dict]]:
        kind, source_id = reference(value)
        if kind == "song":
            return [(source_id, self.song(source_id))]
        return self.playlist(value)


def read_clipboard() -> str:
    """Read clipboard text with the platform's available command-line tool."""
    if sys.platform == "darwin":
        commands = [["pbpaste"]]
    elif sys.platform == "win32":
        commands = [["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-Clipboard -Raw"]]
    else:
        commands = [["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"], ["xsel", "--clipboard", "--output"]]
    available = False
    for command in commands:
        if not shutil.which(command[0]):
            continue
        available = True
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=5, check=True)
        except (OSError, subprocess.SubprocessError, UnicodeError):
            continue
        if result.stdout.strip():
            return result.stdout.strip()
    if available:
        raise SourceError("the clipboard is empty or unavailable; pass a URL directly")
    raise SourceError("no clipboard reader found; install wl-clipboard/xclip or pass a URL directly")
