"""Server-only downloads and ID3 metadata. Optional dependencies are imported lazily."""
import os
import shutil
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen


class DownloadError(Exception):
    pass


def version() -> str | None:
    try:
        from importlib.metadata import version as package_version
        return package_version('yt-dlp')
    except Exception:
        return None


def _cover(url: str) -> tuple[bytes, str]:
    request = Request(url, headers={'User-Agent': 'ytmm/2'})
    with urlopen(request, timeout=30) as response:
        data = response.read(10 * 1024 * 1024 + 1)
        mime = response.headers.get_content_type()
    if len(data) > 10 * 1024 * 1024 or not mime.startswith('image/'):
        raise DownloadError('cover art is not an image or exceeds 10 MiB')
    return data, mime


def _write_tags(path: Path, song: dict) -> None:
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1, TALB, TDRC, TRCK, APIC
    except ImportError as exc:
        raise DownloadError("tagging needs server dependencies: install 'ytmm[server]'") from exc
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    for key, frame, value in (
        ('TIT2', TIT2, song.get('title')),
        ('TPE1', TPE1, song.get('artists') or []),
        ('TALB', TALB, song.get('album')),
        ('TDRC', TDRC, song.get('year')),
        ('TRCK', TRCK, song.get('track')),
    ):
        tags.delall(key)
        if value is not None and value != '' and value != []:
            tags.add(frame(encoding=3, text=value if isinstance(value, list) else [str(value)]))
    if song.get('cover_url'):
        data, mime = _cover(song['cover_url'])
        tags.delall('APIC')
        tags.add(APIC(encoding=3, mime=mime, type=3, desc='Cover', data=data))
    tags.save(path, v2_version=3)


def tag(path: Path, song: dict) -> None:
    """Retag a private copy, then replace, so Syncthing sees a complete file."""
    name = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.ytmm-', suffix='.mp3', dir=path.parent, delete=False) as f:
            name = Path(f.name)
        shutil.copyfile(path, name)
        _write_tags(name, song)
        os.replace(name, path)
    finally:
        if name is not None:
            name.unlink(missing_ok=True)


def download(id: str, song: dict, dst: Path) -> None:
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise DownloadError("downloads need server dependencies: install 'ytmm[server]'") from exc
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Keep incomplete audio and postprocessing hidden and on the same filesystem.
    with tempfile.TemporaryDirectory(prefix='.ytmm-', dir=dst.parent) as directory:
        temp = Path(directory)
        options = {
            'format': 'bestaudio/best',
            'outtmpl': str(temp / 'audio.%(ext)s'),
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'retries': 3,
            'fragment_retries': 3,
            'socket_timeout': 30,
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '5'}],
        }
        try:
            with YoutubeDL(options) as downloader:
                info = downloader.extract_info(f'https://www.youtube.com/watch?v={id}', download=True)
            audio = temp / 'audio.mp3'
            if not info or not audio.is_file() or audio.stat().st_size == 0:
                raise DownloadError('yt-dlp did not produce an MP3 file')
            metadata = dict(song)
            if not metadata.get('cover_url') and info.get('thumbnail'):
                metadata['cover_url'] = info['thumbnail']
            _write_tags(audio, metadata)
            os.replace(audio, dst)
        except DownloadError:
            raise
        except Exception as exc:
            raise DownloadError(str(exc)) from exc


def fake_download(id: str, song: dict, dst: Path, delay: float = 0.0) -> None:
    """Explicit simulation for the historical MVP and offline integration tests."""
    if song.get('title', '').startswith('FAIL'):
        raise DownloadError('simulated failure (title starts with FAIL)')
    time.sleep(delay)
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(f'.{dst.name}.part')
    part.write_text(f'ytmm placeholder\nid: {id}\ntitle: {song.get("title")}\n', encoding='utf-8')
    os.replace(part, dst)
