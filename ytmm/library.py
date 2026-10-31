"""Validated v2 library, atomic persistence and deterministic conflict merging.

Entries are keyed by YouTube video ID. Edits carry a timestamp and device name;
removals are retained as tombstones so offline devices cannot resurrect songs.
"""
import copy
import json
import logging
import math
import os
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

VERSION = 2
LIBRARY_FILE = 'library.json'
CONFLICT_GLOB = 'library.sync-conflict-*.json'
re_video_id = re.compile(r'^[0-9A-Za-z_-]{11}$')
logger = logging.getLogger(__name__)


def parse_id(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if re_video_id.fullmatch(value):
        return value
    try:
        url = urlsplit(value if '://' in value else 'https://' + value)
        host = (url.hostname or '').lower()
        if url.scheme not in ('http', 'https'):
            return None
        if host in ('youtu.be', 'www.youtu.be'):
            candidate = url.path.strip('/')
        elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'):
            parts = url.path.strip('/').split('/')
            if parts[0] == 'watch':
                candidate = parse_qs(url.query).get('v', [''])[0]
            elif len(parts) == 2 and parts[0] in ('shorts', 'embed', 'live'):
                candidate = parts[1]
            else:
                return None
        else:
            return None
        return candidate if re_video_id.fullmatch(candidate) else None
    except ValueError:
        return None


def _key(entry: dict):
    return (entry.get('updated', 0), entry.get('updated_by', ''), json.dumps(entry, sort_keys=True, ensure_ascii=False))


def validate(songs: dict) -> None:
    """Reject incomplete/corrupt input before it can become a destructive plan."""
    if not isinstance(songs, dict):
        raise ValueError('library songs must be an object keyed by video ID')
    for video_id, entry in songs.items():
        if not isinstance(video_id, str) or not re_video_id.fullmatch(video_id):
            raise ValueError(f'invalid library video ID: {video_id!r}')
        if not isinstance(entry, dict):
            raise ValueError(f'entry {video_id} must be an object')
        updated = entry.get('updated')
        if isinstance(updated, bool) or not isinstance(updated, (int, float)) or not math.isfinite(updated) or updated < 0:
            raise ValueError(f'entry {video_id} needs a finite nonnegative updated timestamp')
        if not isinstance(entry.get('updated_by', ''), str):
            raise ValueError(f'entry {video_id}: updated_by must be a string')
        if 'deleted' in entry and not isinstance(entry['deleted'], bool):
            raise ValueError(f'entry {video_id}: deleted must be a boolean')
        if not entry.get('deleted'):
            if not isinstance(entry.get('title'), str) or not entry['title'].strip():
                raise ValueError(f'entry {video_id} needs a title')
            if not isinstance(entry.get('artists'), list) or any(not isinstance(a, str) for a in entry['artists']):
                raise ValueError(f'entry {video_id}: artists must be a list of strings')
            if entry.get('album') is not None and not isinstance(entry['album'], str):
                raise ValueError(f'entry {video_id}: album must be a string')
            if entry.get('year') is not None and (isinstance(entry['year'], bool) or not isinstance(entry['year'], (str, int))):
                raise ValueError(f'entry {video_id}: year must be a year number or string')
            if 'tags' in entry and (not isinstance(entry['tags'], list) or any(not isinstance(t, str) for t in entry['tags'])):
                raise ValueError(f'entry {video_id}: tags must be a list of strings')
    # Also checks nested metadata and NaN/Infinity in optional fields.
    try:
        json.dumps(songs, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'library metadata is not valid JSON: {exc}') from exc


def merge(*song_maps: dict) -> dict:
    merged = {}
    for songs in song_maps:
        validate(songs)
        for video_id, entry in songs.items():
            if video_id not in merged or _key(entry) > _key(merged[video_id]):
                merged[video_id] = copy.deepcopy(entry)
    return merged


def read(path: Path) -> dict:
    path = Path(path)
    try:
        with path.open(encoding='utf-8') as stream:
            data = json.load(stream)
        if not isinstance(data, dict) or data.get('version') != VERSION:
            raise ValueError(f'"{path}" is not a v{VERSION} library')
        songs = data.get('songs')
        validate(songs)
        return songs
    except (ValueError, TypeError) as exc:
        raise ValueError(f'invalid library "{path}": {exc}') from exc


def write(path: Path, songs: dict) -> None:
    validate(songs)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({'version': VERSION, 'songs': songs}, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(text + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def library_lock(path: Path):
    """Serialize local writers. Syncthing conflicts still use per-entry merging."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(f'.{path.name}.lock.tmp').open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0)
            if not stream.read(1):
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class Library:
    def __init__(self, directory: Path, device: str):
        self.path = Path(directory) / LIBRARY_FILE
        self.device = device
        self.songs = {}
        self.merged_conflicts = []

    def _conflicts(self):
        copies = []
        for path in sorted(self.path.parent.glob(CONFLICT_GLOB)):
            try:
                before = path.stat()
                songs = read(path)
                after = path.stat()
                signature = (after.st_ino, after.st_size, after.st_mtime_ns)
                if (before.st_ino, before.st_size, before.st_mtime_ns) == signature:
                    copies.append((path, songs, signature))
            except (OSError, ValueError) as exc:
                logger.warning('leaving unreadable conflict copy "%s": %s', path, exc)
        return copies

    def _save(self, conflicts=()):
        disk = read(self.path) if self.path.exists() else {}
        self.songs = merge(disk, self.songs, *(songs for _, songs, _ in conflicts))
        write(self.path, self.songs)
        for path, _, signature in conflicts:
            try:
                info = path.stat()
                if (info.st_ino, info.st_size, info.st_mtime_ns) == signature:
                    path.unlink()
                    self.merged_conflicts.append(path)
            except FileNotFoundError:
                pass

    def load(self) -> 'Library':
        self.merged_conflicts = []
        with library_lock(self.path):
            self.songs = read(self.path) if self.path.exists() else {}
            conflicts = self._conflicts()
            if conflicts:
                self._save(conflicts)
        return self

    def save(self) -> None:
        with library_lock(self.path):
            self._save(self._conflicts())

    def live(self) -> dict:
        return {video_id: song for video_id, song in self.songs.items() if not song.get('deleted')}

    def _timestamp(self, video_id: str) -> float:
        previous = self.songs.get(video_id, {}).get('updated', 0)
        return max(time.time(), previous + 0.000001)

    def put(self, video_id: str, **fields) -> dict:
        if not isinstance(video_id, str) or not re_video_id.fullmatch(video_id):
            raise ValueError(f'invalid video ID: {video_id!r}')
        entry = copy.deepcopy(self.songs.get(video_id, {}))
        entry.pop('deleted', None)
        entry.update({key: value for key, value in fields.items() if value is not None})
        entry.setdefault('title', video_id)
        entry.setdefault('artists', [])
        entry['updated'] = self._timestamp(video_id)
        entry['updated_by'] = self.device
        validate({video_id: entry})
        self.songs[video_id] = entry
        return entry

    def delete(self, video_id: str) -> None:
        entry = {'deleted': True, 'updated': self._timestamp(video_id), 'updated_by': self.device}
        validate({video_id: entry})
        self.songs[video_id] = entry
