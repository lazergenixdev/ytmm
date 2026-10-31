"""
yt-dlp wrapper and tagging (server only: needs `ytmm[server]`).

yt-dlp runs as a subprocess, so upgrading it (the daily update command) takes
effect without restarting the worker.
"""
import base64, json, re, shlex, shutil, subprocess, sys, time, urllib.request
from pathlib import Path
from .library import parse_id
from .plan import AUDIO_EXTS
from .sources import split_title

TIMEOUT    = 900  # seconds before a download is given up
USER_AGENT = 'Mozilla/5.0 (compatible; ytmm)'
ID_KEYS    = ('ytmm_id', 'purl', 'comment', 'comm', 'woas', 'wxxx', '\xa9cmt')  # tags that may hold the video URL, best first
SERVER_HINT = "install ytmm with the server extra: uv tool install 'ytmm[server]'"


class DownloadError(Exception):
    pass


def error_message(stderr: str, id: str) -> str:
    """The useful part of yt-dlp's last error, e.g. "Video unavailable"."""
    lines = [l.strip() for l in stderr.splitlines() if l.strip()]
    errors = [l for l in lines if l.startswith('ERROR:')]
    msg = (errors or lines or ['yt-dlp failed'])[-1]
    msg = re.sub(r'^ERROR:\s*', '', msg)
    msg = re.sub(rf'^\[[\w:]+\]\s*{re.escape(id)}:\s*', '', msg)
    return msg[:300]


class YtDlp:
    def __init__(self, cfg):
        self.cfg = cfg
        if cfg.ytdlp:
            self.cmd = shlex.split(cfg.ytdlp)
        elif getattr(sys, 'frozen', False):  # the standalone build has no yt-dlp inside
            self.cmd = ['yt-dlp']
        else:
            self.cmd = [sys.executable, '-m', 'yt_dlp']

    def version(self) -> str | None:
        try:
            r = subprocess.run([*self.cmd, '--version'], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0:
            return None
        return r.stdout.strip() or None

    def check(self) -> None:
        """Raises DownloadError if this machine can't download."""
        if self.version() is None:
            raise DownloadError(f'cannot run yt-dlp ({shlex.join(self.cmd)}); {SERVER_HINT}')
        try:
            import mutagen  # noqa: F401
        except ImportError:
            raise DownloadError(f'mutagen is not installed; {SERVER_HINT}')
        if self.cfg.format in ('mp3', 'opus') and not shutil.which('ffmpeg'):
            raise DownloadError(f'ffmpeg is needed to save {self.cfg.format} files (or set format = "m4a")')

    def options(self) -> list[str]:
        if self.cfg.format == 'm4a':
            # YouTube serves AAC audio as m4a: no conversion, and no ffmpeg needed
            convert = ['-x', '--audio-format', 'm4a'] if shutil.which('ffmpeg') else []
            return ['-f', 'bestaudio[ext=m4a]/bestaudio/best', *convert]
        if self.cfg.format == 'opus':
            return ['-f', 'bestaudio[acodec=opus]/bestaudio/best', '-x', '--audio-format', 'opus']
        return ['-f', 'bestaudio/best', '-x', '--audio-format', 'mp3', '--audio-quality', str(self.cfg.quality)]

    def download(self, id: str, song: dict, tmp: Path) -> tuple[Path, dict]:
        """Downloads into the empty folder `tmp`; returns the audio file and yt-dlp's info."""
        cmd = [*self.cmd, '--no-playlist', '--no-progress', *self.options(),
               '--write-info-json', '-o', str(tmp / '%(id)s.%(ext)s'),
               '--print', 'after_move:filepath', *self.cfg.ytdlp_args, '--', id]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            raise DownloadError(f'yt-dlp took longer than {TIMEOUT // 60} minutes')
        except OSError as e:
            raise DownloadError(f'cannot run yt-dlp ({e})')
        if r.returncode != 0:
            raise DownloadError(error_message(r.stderr, id))
        printed = [Path(l.strip()) for l in r.stdout.splitlines() if l.strip()]
        found = [p for p in printed if p.is_file()] or sorted(p for p in tmp.iterdir() if p.suffix.lower() in AUDIO_EXTS)
        if not found:
            raise DownloadError('yt-dlp finished without writing an audio file')
        try:
            info = json.loads((tmp / f'{id}.info.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            info = {}
        return found[-1], info

    def tag(self, path: Path, id: str, song: dict) -> None:
        write_tags(path, id, song, fetch_cover(id, song))


class Fake:
    """
    Writes a small placeholder instead of audio, to test syncing without
    yt-dlp. A song whose title starts with "FAIL" fails.
    """
    def __init__(self, cfg):
        self.cfg = cfg

    def version(self) -> str:
        return 'fake'

    def check(self) -> None:
        pass

    def download(self, id: str, song: dict, tmp: Path) -> tuple[Path, dict]:
        if (song.get('title') or '').startswith('FAIL'):
            raise DownloadError('simulated failure (title starts with FAIL)')
        time.sleep(self.cfg.fake_delay)
        path = tmp / f'{id}.mp3'
        path.write_text(f'ytmm placeholder\nid: {id}\n', encoding='utf-8')
        return path, {}

    def tag(self, path: Path, id: str, song: dict) -> None:
        pass


def make(cfg):
    return Fake(cfg) if cfg.downloader == 'fake' else YtDlp(cfg)


def meta_from_info(info: dict) -> dict:
    """Song fields from yt-dlp's info, for songs that were added without metadata."""
    if info.get('track'):
        artist = info.get('artist') or info.get('creator')
        meta = {'title': info['track'], 'artists': info.get('artists') or ([artist] if artist else [])}
        if info.get('album'):
            meta['album'] = info['album']
        year = info.get('release_year')
        if not year and (m := re.search(r'Released on: (\d{4})', info.get('description') or '')):
            year = m.group(1)
        if year:
            meta['year'] = int(year)
        return meta
    if info.get('title'):
        channel = info.get('uploader') or info.get('channel')
        title, artists = split_title(info['title'], [channel] if channel else [])
        return {'title': title, 'artists': artists}
    return {}


#
# Cover art
#

def fetch(url: str, timeout: float = 20) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except (OSError, ValueError):
        return None


def square(jpeg: bytes) -> bytes:
    """
    The centered square of a video thumbnail. Album tracks show their art
    pillarboxed in 16:9 (or letterboxed in 4:3), so this cuts out exactly the art.
    """
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        return jpeg
    side = "'min(iw*9/16,ih)'"
    try:
        r = subprocess.run([ffmpeg, '-v', 'error', '-i', 'pipe:0', '-vf', f'crop={side}:{side}',
                            '-q:v', '2', '-f', 'mjpeg', 'pipe:1'], input=jpeg, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return jpeg
    return r.stdout if r.returncode == 0 and r.stdout else jpeg


def fetch_cover(id: str, song: dict) -> bytes | None:
    if song.get('cover') and (data := fetch(song['cover'])):
        return data
    for name in ('maxresdefault', 'hqdefault'):
        if data := fetch(f'https://i.ytimg.com/vi/{id}/{name}.jpg'):
            return square(data)
    return None


#
# Tags
#

def write_tags(path: Path, id: str, song: dict, cover: bytes | None) -> None:
    """Replace the file's tags with the library's metadata and cover art."""
    title   = song.get('title') or id
    artists = song.get('artists') or []
    artist  = ', '.join(artists)
    album, year = song.get('album'), song.get('year')
    ext = path.suffix.lower()
    if ext == '.mp3':
        from mutagen.id3 import ID3, APIC, TALB, TDRC, TIT2, TPE1, TPE2, TXXX, WOAS
        tags = ID3()
        tags.add(TIT2(encoding=3, text=title))
        if artist:
            tags.add(TPE1(encoding=3, text=artist))
        if album:
            tags.add(TALB(encoding=3, text=album))
            if artists:
                tags.add(TPE2(encoding=3, text=artists[0]))
        if year:
            tags.add(TDRC(encoding=3, text=str(year)))
        tags.add(TXXX(encoding=3, desc='ytmm_id', text=id))
        tags.add(WOAS(url=f'https://music.youtube.com/watch?v={id}'))
        if cover:
            tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover))
        tags.save(path, v1=0, v2_version=3)  # ID3v2.3 is what Android and Windows read best
    elif ext == '.m4a':
        from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
        f = MP4(path)
        if f.tags is None:
            f.add_tags()
        f.tags.clear()
        f.tags['\xa9nam'] = [title]
        if artist:
            f.tags['\xa9ART'] = [artist]
        if album:
            f.tags['\xa9alb'] = [album]
            if artists:
                f.tags['aART'] = [artists[0]]
        if year:
            f.tags['\xa9day'] = [str(year)]
        f.tags['----:com.apple.iTunes:ytmm_id'] = [MP4FreeForm(id.encode())]
        if cover:
            f.tags['covr'] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
        f.save()
    elif ext in ('.opus', '.ogg'):
        import mutagen
        from mutagen.flac import Picture
        f = mutagen.File(path)
        for key in list(f.tags.keys()):
            del f.tags[key]
        f.tags['title'] = [title]
        if artists:
            f.tags['artist'] = artists
        if album:
            f.tags['album'] = [album]
            if artists:
                f.tags['albumartist'] = [artists[0]]
        if year:
            f.tags['date'] = [str(year)]
        f.tags['ytmm_id'] = [id]
        if cover:
            pic = Picture()
            pic.type, pic.mime, pic.data = 3, 'image/jpeg', cover
            f.tags['metadata_block_picture'] = [base64.b64encode(pic.write()).decode('ascii')]
        f.save()
    else:
        raise DownloadError(f'cannot tag {ext} files')


def _values(value) -> list:
    if hasattr(value, 'text'):
        return list(value.text)
    if hasattr(value, 'url'):
        return [value.url]
    return list(value) if isinstance(value, list) else [value]


def read_id(path: Path) -> str | None:
    """The video id stored in a file's tags, by ytmm or by yt-dlp's metadata step (v0.2 files)."""
    try:
        import mutagen
        f = mutagen.File(path)
        tags = f.tags if f is not None else None
    except Exception:  # e.g. damaged audio: the ID3 tag may still be readable
        tags = None
    if not tags and path.suffix.lower() == '.mp3':
        try:
            from mutagen.id3 import ID3
            tags = ID3(path)
        except Exception:
            return None
    if not tags:
        return None
    found = {}
    for key, value in tags.items():
        k = key.lower()
        rank = next((i for i, name in enumerate(ID_KEYS) if name in k), None)
        if rank is None or 'description' in k or 'synopsis' in k:  # descriptions link to other videos
            continue
        for v in _values(value):
            v = v.decode('utf-8', 'replace') if isinstance(v, bytes) else str(v)
            if id := parse_id(v):
                found.setdefault(rank, id)
    return found[min(found)] if found else None


def tool_versions(cfg) -> dict[str, str | None]:
    """Versions of what downloading needs, for `ytmm canary`."""
    def version(cmd, pattern):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        m = re.search(pattern, r.stdout or '')
        return m.group(1) if r.returncode == 0 and m else None
    try:
        import mutagen
        mutagen_version = mutagen.version_string
    except ImportError:
        mutagen_version = None
    return {
        'yt-dlp':  make(cfg).version(),
        'ffmpeg':  version(['ffmpeg', '-version'], r'version (\S+)'),
        'deno':    version(['deno', '--version'], r'deno (\S+)'),
        'mutagen': mutagen_version,
    }
