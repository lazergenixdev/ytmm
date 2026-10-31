"""Library + files on disk -> actions. Pure: no filesystem access."""
import hashlib, json, re, unicodedata
from dataclasses import dataclass
from .library import describe

AUDIO_EXTS     = ('.mp3', '.m4a', '.opus', '.ogg', '.webm', '.flac', '.wav', '.aac')
PLAYLIST_EXT   = '.m3u'
MAX_NAME_BYTES = 200  # file systems allow 255; leave room for " [id].ext" and Syncthing's temp names
TAG_VERSION    = 1    # bump when the tags written into files change, to retag everything

re_file_id = re.compile(r'\[([0-9A-Za-z_-]{11})\]\.[^.]+$')
re_unsafe  = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
re_space   = re.compile(r'\s+')


def safe_name(name: str) -> str:
    name = re_space.sub(' ', re_unsafe.sub('', name)).strip().lstrip('.')
    name = unicodedata.normalize('NFC', name).encode('utf-8')[:MAX_NAME_BYTES]
    return name.decode('utf-8', errors='ignore').rstrip(' .')


def file_name(id: str, song: dict, ext: str = '.mp3') -> str:
    name = safe_name(describe(id, song))
    return f'{name} [{id}]{ext}' if name else f'[{id}]{ext}'


def file_id(name: str) -> str | None:
    m = re_file_id.search(name)
    return m.group(1) if m else None


def ext_of(name: str) -> str:
    return name[name.rfind('.'):].lower() if '.' in name else ''


def tag_hash(id: str, song: dict) -> str:
    """Changes whenever something written into the file's tags changes."""
    fields = {k: song.get(k) for k in ('title', 'artists', 'album', 'year', 'cover')}
    text = json.dumps([TAG_VERSION, id, fields], sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]


@dataclass
class Action:
    kind: str               # 'trash' | 'rename' | 'retag' | 'download'
    id:   str | None
    src:  str | None = None
    dst:  str | None = None
    why:  str = ''


def plan(songs: dict, files: list[str], tagged: dict | None = None, ext: str = '.mp3') -> list[Action]:
    """
    `tagged` maps video ids to the tag_hash() last written into their file;
    files whose song changed since are retagged (None: never retag).
    """
    trash, rename, retag, download = [], [], [], []

    by_id = {}
    for name in sorted(files):
        id = file_id(name)
        if id is None:
            trash.append(Action('trash', None, src=name, why='no video id in file name'))
        else:
            by_id.setdefault(id, []).append(name)

    for id, names in by_id.items():
        song = songs.get(id)
        if song is None:
            trash += [Action('trash', id, src=n, why='not in library') for n in names]
            continue
        if song.get('deleted'):
            trash += [Action('trash', id, src=n, why='deleted') for n in names]
            continue
        keep = next((n for n in names if n == file_name(id, song, ext_of(n))), names[0])
        trash += [Action('trash', id, src=n, why='duplicate') for n in names if n != keep]
        want = file_name(id, song, ext_of(keep))
        if keep != want:
            rename.append(Action('rename', id, src=keep, dst=want))
        if tagged is not None and tagged.get(id) != tag_hash(id, song):
            retag.append(Action('retag', id, src=want))

    # newest first, so new songs show up quickly even behind a long backlog
    for id, song in sorted(songs.items(), key=lambda kv: (-kv[1].get('added', 0), kv[0])):
        if not song.get('deleted') and id not in by_id:
            download.append(Action('download', id, dst=file_name(id, song, ext)))

    return trash + rename + retag + download


def playlist_name(tag: str) -> str:
    name = safe_name(tag).replace(' ', '_')
    return f'{name or "_"}{PLAYLIST_EXT}'


def playlists(songs: dict, names: dict[str, str]) -> dict[str, str]:
    """
    One .m3u per tag: {file name: content}. `names` maps the ids of songs that
    have a file to its name in songs/; songs without one are left out.
    """
    by_name = {}
    for id, song in sorted(songs.items(), key=lambda kv: (kv[1].get('added', 0), kv[0])):
        if song.get('deleted') or id not in names:
            continue
        for tag in song.get('tags') or []:
            by_name.setdefault(playlist_name(tag), (tag, []))[1].append(id)
    out = {}
    for name, (tag, ids) in sorted(by_name.items()):
        lines = ['#EXTM3U', f'#PLAYLIST:{tag}']
        for id in ids:
            lines += [f'#EXTINF:-1,{describe(id, songs[id])}', f'../songs/{names[id]}']
        out[name] = '\n'.join(lines) + '\n'
    return out
