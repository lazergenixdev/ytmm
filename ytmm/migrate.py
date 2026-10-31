"""
`ytmm migrate`: convert a v0.2 music.json into library.json, and move the files
it downloaded to ID-based names so the server doesn't download them again.
"""
import re
from pathlib import Path
from .library import normalize_tag, parse_id
from .plan import file_id

re_invalid = re.compile(r'[^ 0-9A-Za-z_]')
re_space   = re.compile(r'\s+')
re_garbage = re.compile(r'\(Official .*\)|\(From .*\)|\(feat\. .*\)')


def v0_file_name(title: str) -> str:
    """The name v0.2 gave a song's file, without the extension."""
    title = re_garbage.sub('', title).strip()
    title = re_invalid.sub('', title).strip()
    title = re_space.sub(' ', title)
    return title.lower().replace(' ', '_')


def convert(db: dict, now: float) -> dict:
    """
    v0.2 entries -> songs (without updated/updated_by). Playlists become tags,
    and the list order (newest last) is kept as `added`.
    """
    tags = {}
    for name, ids in (db.get('playlists') or {}).items():
        if tag := normalize_tag(name):
            for id in ids:
                tags.setdefault(id, set()).add(tag)
    entries = [e for e in db.get('data') or []
               if isinstance(e, dict) and isinstance(e.get('id'), str) and parse_id(e['id']) == e['id']]
    songs = {}
    for i, e in enumerate(entries):
        id = e['id']
        if id in songs:
            continue
        song = {'title': e.get('title') or id, 'artists': [a for a in e.get('artists') or [] if a]}
        if e.get('album'):
            song['album'] = e['album']
        if isinstance(e.get('year'), int) and e['year'] > 0:
            song['year'] = e['year']
        if tags.get(id):
            song['tags'] = sorted(tags[id])
        song['added'] = now - (len(entries) - i)
        songs[id] = song
    return songs


def files_dir(music_json: Path, db: dict) -> Path | None:
    """v0.2 resolved `root` against the folder it ran in: music.json's folder or the one above."""
    root = Path(db.get('root') or 'music').expanduser()
    if root.is_absolute():
        return root if root.is_dir() else None
    for base in (music_json.parent, music_json.parent.parent, Path.cwd()):
        if (base / root).is_dir():
            return (base / root).resolve()
    return None


def match_files(songs: dict, paths: list[Path], read_id=None) -> tuple[dict[Path, str], list[Path]]:
    """
    The video id of each file: from a v2 name, an `<id>.mp3` name (downloaded
    but never renamed), the tags yt-dlp embedded (`read_id`), or else v0.2's
    title-based name if only one song has it (two songs with the same title
    shared a file name in v0.2). Returns ({file: id}, files left unmatched).
    """
    by_name = {}
    for id, song in songs.items():
        by_name.setdefault(v0_file_name(song.get('title') or '') or id, set()).add(id)
    found, unmatched, taken = {}, [], set()
    for p in sorted(paths, key=lambda p: (file_id(p.name) is None, p)):  # files with an id in the name first
        id = file_id(p.name)
        if id is None and p.stem in songs:
            id = p.stem
        if id is None and read_id:
            id = read_id(p)
        if id is None and len(by_name.get(p.stem, ())) == 1:
            id = next(iter(by_name[p.stem]))
        if id in songs and id not in taken:
            found[p] = id
            taken.add(id)
        else:
            unmatched.append(p)
    return found, unmatched
