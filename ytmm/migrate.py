"""One-way, resumable migration of the v0.2 music.json database.

The source database remains untouched. Existing destinations and tombstones are
never replaced. A dry run performs validation without creating any files.
"""
import json
import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from .config import Config
from .library import CONFLICT_GLOB, LIBRARY_FILE, Library, merge, parse_id, read, validate
from .plan import file_name
from .utils import file_name_from_title


def _legacy_root(source: Path, data: dict, music_dir: Path | None) -> Path:
    if music_dir is not None:
        return Path(music_dir).expanduser().resolve()
    value = data.get('root', 'music')
    if not isinstance(value, str) or not value.strip():
        raise ValueError('legacy root must be a filesystem path (or pass --music-dir)')
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    candidates = { (source.parent / path).resolve(), path.resolve() }
    # Legacy versions commonly kept music.json inside their output directory,
    # but recorded that directory relative to the original working directory.
    if path.name == source.parent.name:
        candidates.add(source.parent)
    existing = sorted(candidate for candidate in candidates if candidate.is_dir())
    if len(existing) > 1:
        raise ValueError('legacy root is ambiguous; pass --music-dir with the directory containing the old MP3 files')
    return existing[0] if existing else (source.parent / path).resolve()


def _entries(data: dict) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get('data'), list):
        raise ValueError('legacy database must contain a data list')
    playlists = data.get('playlists', {})
    if not isinstance(playlists, dict) or any(not isinstance(name, str) or not isinstance(ids, list) for name, ids in playlists.items()):
        raise ValueError('legacy playlists must map playlist names to lists of video IDs')
    entries = {}
    for index, row in enumerate(data['data']):
        if not isinstance(row, dict):
            raise ValueError(f'legacy entry {index + 1} must be an object')
        video_id = parse_id(row.get('id'))
        if not video_id:
            raise ValueError(f'legacy entry {index + 1} has an invalid video ID')
        fields = {key: value for key, value in row.items()
                  if key not in ('id', 'path', 'root', 'updated', 'updated_by', 'deleted') and value is not None}
        fields.setdefault('title', video_id)
        fields.setdefault('artists', [])
        tags = fields.get('tags', [])
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise ValueError(f'legacy entry {video_id} has invalid tags')
        tags = sorted(set(tags) | {name for name, ids in playlists.items() if video_id in ids})
        if tags:
            fields['tags'] = tags
        validate({video_id: {**fields, 'updated': 0, 'updated_by': 'migration'}})
        if video_id in entries and entries[video_id] != fields:
            raise ValueError(f'legacy database has conflicting entries for {video_id}')
        entries[video_id] = fields
    return entries


def _move_exclusive(source: Path, destination: Path) -> None:
    """Publish complete audio without replacing a file or exposing a partial copy."""
    try:
        os.link(source, destination)
    except FileExistsError:
        raise
    except OSError:
        # Cross-filesystem copies must finish outside the final filename. A hard
        # crash can leave this ignored temporary file, never a partial MP3 that
        # a resumed migration or worker would mistake for completed audio.
        descriptor, name = tempfile.mkstemp(prefix='.ytmm-migrate-', suffix='.tmp', dir=destination.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, 'wb') as new, source.open('rb') as old:
                shutil.copyfileobj(old, new)
                new.flush()
                os.fsync(new.fileno())
            shutil.copystat(source, temporary)
            try:
                os.link(temporary, destination)
            except FileExistsError:
                raise
            except OSError as exc:
                if os.name == 'nt':
                    # On Windows rename rejects an existing destination.
                    os.rename(temporary, destination)
                else:
                    raise OSError('safe migration requires hard-link support on the destination filesystem; original audio preserved') from exc
        finally:
            temporary.unlink(missing_ok=True)
    source.unlink()


def migrate(cfg: Config, source: Path, *, dry_run: bool = False,
            music_dir: Path | None = None) -> dict:
    if cfg.role != 'server':
        raise ValueError('migration must run on a device with role = "server"')
    source = Path(source).expanduser().resolve()
    with source.open(encoding='utf-8') as stream:
        data = json.load(stream)
    entries = _entries(data)
    old_root = _legacy_root(source, data, music_dir)
    report = {'added': [], 'moved': [], 'missing': [], 'skipped': [], 'dry_run': dry_run}

    def apply():
        # Avoid writes of lock files or merged conflicts during a dry run.
        lib = Library(cfg.library_dir, cfg.device)
        if dry_run:
            path = cfg.library_dir / LIBRARY_FILE
            lib.songs = read(path) if path.exists() else {}
            for conflict in sorted(cfg.library_dir.glob(CONFLICT_GLOB)):
                lib.songs = merge(lib.songs, read(conflict))
        else:
            lib.load()
        for video_id, fields in entries.items():
            if video_id not in lib.songs:
                lib.put(video_id, **fields)
                report['added'].append(video_id)
            else:
                report['skipped'].append(f'{video_id}: existing library entry preserved')
        # Persist every entry before moving audio; interrupted runs can resume.
        if not dry_run:
            lib.save()
            cfg.songs_dir.mkdir(parents=True, exist_ok=True)
        legacy_names = Counter(file_name_from_title(song['title']) + '.mp3' for song in entries.values())
        for video_id, fields in entries.items():
            song = lib.songs[video_id]
            if song.get('deleted'):
                continue
            destination = cfg.songs_dir / file_name(video_id, song)
            legacy_name = file_name_from_title(fields['title']) + '.mp3'
            # ID-named files left by interrupted v0.2 downloads are unambiguous.
            id_source = old_root / f'{video_id}.mp3'
            old = id_source if id_source.is_file() else old_root / legacy_name
            if destination.exists():
                if old.is_file() and old != destination:
                    report['skipped'].append(f'{video_id}: destination exists; original file preserved')
                continue
            if not old.is_file():
                report['missing'].append(video_id)
                continue
            if old.is_symlink():
                report['skipped'].append(f'{video_id}: symbolic link preserved; import the file manually')
                report['missing'].append(video_id)
                continue
            if old != id_source and legacy_names[legacy_name] > 1:
                report['skipped'].append(f'{video_id}: old filename is shared by multiple songs; original file preserved')
                report['missing'].append(video_id)
                continue
            if not dry_run:
                _move_exclusive(old, destination)
            report['moved'].append((str(old), str(destination)))
        return report

    if dry_run:
        return apply()
    # The worker and migration must never write audio simultaneously.
    from .worker import worker_lock
    with worker_lock(cfg):
        return apply()
