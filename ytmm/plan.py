"""Library + files on disk -> actions. Pure: no filesystem access."""
import re
from dataclasses import dataclass

EXT = '.mp3'

re_file_id = re.compile(r'\[([0-9A-Za-z_-]{11})\]\.[^.]+$')
re_unsafe  = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
re_space   = re.compile(r'\s+')


def file_name(id: str, song: dict) -> str:
    title = song.get('title') or id
    artists = ', '.join(song.get('artists') or [])
    name = f'{artists} - {title}' if artists else title
    name = re_space.sub(' ', re_unsafe.sub('', name)).strip().lstrip('.')[:180]
    return f'{name} [{id}]{EXT}'


def file_id(name: str) -> str | None:
    m = re_file_id.search(name)
    return m.group(1) if m else None


@dataclass
class Action:
    kind: str               # 'trash' | 'rename' | 'download'
    id:   str | None
    src:  str | None = None
    dst:  str | None = None
    why:  str = ''


def plan(songs: dict, files: list[str]) -> list[Action]:
    trash, rename, download = [], [], []

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
        want = file_name(id, song)
        keep = want if want in names else names[0]
        trash += [Action('trash', id, src=n, why='duplicate') for n in names if n != keep]
        if keep != want:
            rename.append(Action('rename', id, src=keep, dst=want))

    for id, song in sorted(songs.items()):
        if not song.get('deleted') and id not in by_id:
            download.append(Action('download', id, dst=file_name(id, song)))

    return trash + rename + download
