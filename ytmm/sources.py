"""
Song metadata from YouTube Music through ytmusicapi (no yt-dlp needed): video
lookup, search, playlists and albums, the clipboard, and the inbox playlist.
"""
import functools, os, re, shutil, subprocess, sys, time
from dataclasses import dataclass, field
from .library import parse_id, re_video_id

COVER_SIZE = 720  # px; matches the square the server crops from video thumbnails

re_url      = re.compile(r'https?://[^\s<>"\']+')
re_list     = re.compile(r'[?&]list=([0-9A-Za-z_-]{10,})')
re_browse   = re.compile(r'/browse/(MPREb_[0-9A-Za-z_-]+)')
re_list_id  = re.compile(r'^(?:VL)?((?:PL|OLAK5uy_|RD|UU|LL|FL)[0-9A-Za-z_-]{10,})$')
re_size     = re.compile(r'=w\d+-h\d+')
re_year     = re.compile(r'^\d{4}$')
re_garbage  = re.compile(r'\s*[(\[](?:official|lyrics?|audio|video|visuali[sz]er|music video|hd|hq|4k|mv|m/v)\b[^)\]]*[)\]]', re.I)
re_topic    = re.compile(r'\s*-\s*Topic$')


class SourceError(Exception):
    pass


@dataclass
class Track:
    id:       str
    title:    str
    artists:  list[str] = field(default_factory=list)
    album:    str | None = None
    album_id: str | None = None
    year:     int | None = None
    cover:    str | None = None
    duration: str | None = None
    video:    bool = False  # a video upload rather than an album track

    def fields(self) -> dict:
        """What goes into library.json."""
        d = {'title': self.title, 'artists': self.artists, 'album': self.album, 'year': self.year, 'cover': self.cover}
        return {k: v for k, v in d.items() if v not in (None, '')}


_yt = None


class _Retrying:
    """Retries a YTMusic call once when the connection drops."""
    def __init__(self, yt):
        self.yt = yt

    def __getattr__(self, name):
        method = getattr(self.yt, name)
        def call(*args, **kwargs):
            import requests
            try:
                return method(*args, **kwargs)
            except (requests.ConnectionError, requests.Timeout):
                time.sleep(1)
                return method(*args, **kwargs)
        return call


def api():
    global _yt
    if _yt is None:
        from ytmusicapi import YTMusic
        _yt = _Retrying(YTMusic())
    return _yt


def classify(arg: str) -> tuple[str | None, str]:
    """('video', id), ('playlist', list id), ('album', browse id), or (None, arg) for a search query."""
    s = arg.strip()
    if re_url.match(s) or 'youtube.com/' in s or 'youtu.be/' in s:
        if '/playlist' in s and (m := re_list.search(s)):
            return 'playlist', m.group(1)
        if m := re_browse.search(s):
            return 'album', m.group(1)
        if id := parse_id(s):
            return 'video', id
        if m := re_list.search(s):
            return 'playlist', m.group(1)
        return None, s
    if re_video_id.match(s):
        return 'video', s
    if m := re_list_id.match(s):
        return 'playlist', m.group(1)
    if s.startswith('MPREb_'):
        return 'album', s
    return None, s


def split_title(title: str, artists: list[str]) -> tuple[str, list[str]]:
    """Clean up a plain video upload's title: "Artist - Song (Official Video)"."""
    clean = re_garbage.sub('', title).strip() or title
    for sep in (' - ', ' – ', ' — '):
        if sep in clean:
            left, _, right = clean.partition(sep)
            names = [a.strip() for a in left.split(',') if a.strip()]
            if right.strip() and names:
                return right.strip(), names
    return clean, [re_topic.sub('', a) for a in artists]


def best_cover(thumbnails) -> str | None:
    """
    Square cover art (album tracks have it on googleusercontent.com) resized to
    COVER_SIZE. Video thumbnails are left out: the server crops those itself.
    """
    for t in reversed(thumbnails or []):
        url = t.get('url') or ''
        if 'googleusercontent.com' in url and re_size.search(url):
            return re_size.sub(f'=w{COVER_SIZE}-h{COVER_SIZE}', url, count=1)
    return None


def to_year(value) -> int | None:
    return int(value) if value and re_year.match(str(value)) else None


def to_track(t: dict) -> Track | None:
    """A ytmusicapi track (playlist, album, search or watch result)."""
    id = t.get('videoId')
    if not id or t.get('isAvailable') is False:
        return None
    title = t.get('title') or id
    artists = [a['name'] for a in t.get('artists') or [] if a.get('name')]
    album = t.get('album')
    kind = t.get('videoType')
    if kind == 'MUSIC_VIDEO_TYPE_UGC':
        title, artists = split_title(title, artists)
    return Track(
        id, title, artists,
        album=album.get('name') if isinstance(album, dict) else album,
        album_id=album.get('id') if isinstance(album, dict) else None,
        year=to_year(t.get('year')),
        cover=best_cover(t.get('thumbnails') or t.get('thumbnail')),
        duration=t.get('duration') or t.get('length'),
        video=kind not in (None, 'MUSIC_VIDEO_TYPE_ATV'),
    )


def lookup(id: str) -> Track | None:
    """Metadata for one video, or None if it doesn't exist."""
    from ytmusicapi.exceptions import YTMusicServerError
    try:
        tracks = api().get_watch_playlist(videoId=id, limit=1).get('tracks') or []
    except YTMusicServerError:
        return None
    except Exception as e:
        raise SourceError(f'YouTube Music lookup failed ({e})') from e
    if not tracks or tracks[0].get('videoId') != id:
        return None
    return to_track(tracks[0])


def search(query: str, videos: bool = False, limit: int = 10) -> list[Track]:
    try:
        results = api().search(query, filter='videos' if videos else 'songs', limit=limit)
    except Exception as e:
        raise SourceError(f'YouTube Music search failed ({e})') from e
    return [t for r in results if (t := to_track(r))][:limit]


@functools.lru_cache(maxsize=None)
def _album(browse_id: str) -> dict:
    return api().get_album(browse_id)


def album(browse_id: str) -> tuple[str, list[Track]]:
    try:
        a = _album(browse_id)
    except Exception as e:
        raise SourceError(f'could not read album {browse_id} ({e})') from e
    items = a.get('tracks') or []
    if a.get('audioPlaylistId'):
        # the album's own track list may point at music videos; its audio playlist has the album tracks
        try:
            items = api().get_playlist(a['audioPlaylistId'], limit=None).get('tracks') or items
        except Exception:
            pass
    title, year, cover = a.get('title') or browse_id, to_year(a.get('year')), best_cover(a.get('thumbnails'))
    tracks = []
    for item in items:
        if t := to_track(item):
            t.album, t.album_id, t.video = title, browse_id, False
            t.year = t.year or year
            t.cover = cover or t.cover
            tracks.append(t)
    artists = ', '.join(x['name'] for x in a.get('artists') or [] if x.get('name'))
    label = ' · '.join(str(x) for x in (artists, year) if x)
    return f'{title} ({label})' if label else title, tracks


def playlist(list_id: str) -> tuple[str, list[Track]]:
    if list_id.startswith('OLAK5uy_'):  # an album's playlist: read the album for its year and cover
        try:
            browse_id = api().get_album_browse_id(list_id)
        except Exception:
            browse_id = None
        if browse_id:
            return album(browse_id)
    try:
        p = api().get_playlist(list_id, limit=None)
    except Exception as e:
        raise SourceError(f'could not read playlist {list_id} ({e})') from e
    return p.get('title') or list_id, [t for item in p.get('tracks') or [] if (t := to_track(item))]


def album_year(browse_id: str) -> int | None:
    try:
        return to_year(_album(browse_id).get('year'))
    except Exception:
        return None


def complete(tracks: list[Track]) -> list[Track]:
    """Fill in years from the albums (playlist and search results don't have them)."""
    for t in tracks:
        if t.year is None and t.album_id:
            t.year = album_year(t.album_id)
    return tracks


def clipboard() -> str:
    if sys.platform == 'darwin':
        cmds = [['pbpaste']]
    elif sys.platform == 'win32':
        cmds = [['powershell', '-NoProfile', '-Command', 'Get-Clipboard']]
    else:
        cmds = [['wl-paste', '--no-newline']] if os.environ.get('WAYLAND_DISPLAY') else []
        cmds += [['xclip', '-selection', 'clipboard', '-o'], ['xsel', '--clipboard', '--output']]
    for cmd in cmds:
        if shutil.which(cmd[0]):
            r = subprocess.run(cmd, capture_output=True, timeout=10)
            if r.returncode == 0:
                return r.stdout.decode('utf-8', errors='replace')
    raise SourceError('could not read the clipboard (on Linux, install wl-clipboard, xclip or xsel)')


def find_urls(text: str) -> list[str]:
    """YouTube (Music) URLs in some text, e.g. the clipboard."""
    urls = [u.rstrip(').,;]') for u in re_url.findall(text)]
    urls = [u for u in urls if classify(u)[0]]
    if not urls and re_video_id.match(text.strip()):
        urls = [text.strip()]
    return list(dict.fromkeys(urls))


def inbox_tracks(url: str) -> list[Track]:
    kind, value = classify(url)
    if kind == 'playlist':
        return playlist(value)[1]
    if kind == 'album':
        return album(value)[1]
    raise SourceError(f'inbox is not a playlist URL: {url}')


def inbox_new(songs: dict, prev_ids: list[str] | None, tracks: list[Track]) -> list[Track]:
    """
    Tracks to import from the inbox playlist. `prev_ids` is what the playlist
    held at the last check (None on the first one). A song is imported when it
    newly appears in the playlist and isn't in the library. A song deleted from
    the library comes back only if it's re-added to the playlist later, so a
    deletion on a computer sticks even though the song stays in the playlist.
    """
    prev = set(prev_ids) if prev_ids is not None else None
    new = []
    for t in tracks:
        if prev is not None and t.id in prev:
            continue
        song = songs.get(t.id)
        if song is None or (song.get('deleted') and prev is not None):
            new.append(t)
    return list({t.id: t for t in new}.values())
