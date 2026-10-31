"""Pure reconciliation of the shared library and the server's audio files."""
import hashlib
import json
import re
from dataclasses import dataclass

EXT = '.mp3'
re_file_id = re.compile(r'\[([0-9A-Za-z_-]{11})\]\.mp3$', re.IGNORECASE)
re_unsafe = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
re_space = re.compile(r'\s+')


def file_name(id: str, song: dict) -> str:
    if not re.fullmatch(r'[0-9A-Za-z_-]{11}', id):
        raise ValueError(f'invalid video id: {id}')
    title = song.get('title') or id
    artists = ', '.join(song.get('artists') or [])
    name = f'{artists} - {title}' if artists else title
    name = re_space.sub(' ', re_unsafe.sub('', name)).strip().lstrip('.')
    # Filesystem limits count bytes, not Unicode characters.
    name = name.encode('utf-8')[:180].decode('utf-8', errors='ignore').rstrip(' .') or id
    return f'{name} [{id}]{EXT}'


def file_id(name: str) -> str | None:
    m = re_file_id.search(name)
    return m.group(1) if m else None


def metadata_key(song: dict) -> str:
    fields = {key: song.get(key) for key in ('title', 'artists', 'album', 'year', 'track', 'cover_url')}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass
class Action:
    kind: str
    id: str | None
    src: str | None = None
    dst: str | None = None
    why: str = ''


def plan(songs: dict, files: list[str], tagged: dict | None = None) -> list[Action]:
    trash, rename, retag, downloads = [], [], [], []
    by_id = {}
    for name in sorted(files):
        id = file_id(name)
        if id is None:
            trash.append(Action('trash', None, src=name, why='no video id in file name'))
        else:
            by_id.setdefault(id, []).append(name)
    for id, names in by_id.items():
        song = songs.get(id)
        if song is None or song.get('deleted'):
            why = 'deleted' if song else 'not in library'
            trash += [Action('trash', id, src=n, why=why) for n in names]
            continue
        want = file_name(id, song)
        keep = want if want in names else names[0]
        trash += [Action('trash', id, src=n, why='duplicate') for n in names if n != keep]
        if keep != want:
            rename.append(Action('rename', id, src=keep, dst=want))
        if tagged is not None and tagged.get(id) != metadata_key(song):
            retag.append(Action('retag', id, src=want))
    for id, song in sorted(songs.items()):
        if not song.get('deleted') and id not in by_id:
            downloads.append(Action('download', id, dst=file_name(id, song)))
    return trash + rename + retag + downloads
