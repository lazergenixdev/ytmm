"""The server reconciles synced metadata with audio; clients only edit the library."""
import hashlib
import logging
import os
import shutil
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.request import Request, urlopen

from . import download, status
from .config import Config
from .library import Library, LIBRARY_FILE, CONFLICT_GLOB, library_lock, read
from .plan import plan, file_id, file_name, metadata_key, re_unsafe

HEARTBEAT = 300
TRASH_DAYS = 30
logger = logging.getLogger(__name__)


def _state_dir(cfg: Config) -> Path:
    fallback = cfg.trash.parent / ('.ytmm-state-' + hashlib.sha256(str(cfg.root).encode()).hexdigest()[:12])
    return getattr(cfg, 'state_dir', fallback)


@contextmanager
def worker_lock(cfg: Config):
    """OS locks disappear when a worker crashes; the lock file stays private."""
    directory = _state_dir(cfg)
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / 'worker.lock', 'a+b') as stream:
        try:
            if os.name == 'nt':
                import msvcrt
                stream.seek(0)
                if not stream.read(1):
                    stream.write(b'0')
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('another worker is already running for this library') from exc
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def scan(songs_dir: Path) -> list[str]:
    if not songs_dir.exists():
        return []
    return sorted(e.name for e in os.scandir(songs_dir)
                  if e.is_file(follow_symlinks=False) and not e.name.startswith('.'))


def move_to_trash(src: Path, trash_dir: Path) -> Path:
    trash_dir.mkdir(parents=True, exist_ok=True)
    dst = trash_dir / src.name
    n = 1
    while dst.exists():
        dst = trash_dir / f'{src.stem} ({n}){src.suffix}'
        n += 1
    shutil.move(src, dst)
    os.utime(dst)
    return dst


def rename_song(source: Path, target: Path) -> None:
    """Allow case-only changes without overwriting a different audio file."""
    if target.exists():
        if not source.samefile(target):
            raise FileExistsError(f'rename destination already exists: {target.name}')
        # Windows and some macOS filesystems need a different intermediate name
        # to apply changes to capitalization. Keep it visible to reconciliation
        # and matched by ID if the process stops between the two renames.
        temporary = source.with_name(f'ytmm-rename-{uuid.uuid4().hex} [{file_id(source.name)}].mp3')
        if temporary.exists():
            raise FileExistsError(f'rename temporary file already exists: {temporary.name}')
        source.rename(temporary)
        try:
            temporary.rename(target)
        except BaseException:
            temporary.rename(source)
            raise
    else:
        source.rename(target)


def purge_trash(trash_dir: Path, days: int = TRASH_DAYS) -> None:
    if not trash_dir.exists():
        return
    cutoff = time.time() - days * 86400
    for entry in os.scandir(trash_dir):
        if entry.is_file(follow_symlinks=False) and entry.stat().st_mtime < cutoff:
            os.remove(entry.path)


def _atomic_text(path: Path, text: str) -> None:
    if path.exists() and path.read_text(encoding='utf-8') == text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='.ytmm-', suffix='.tmp', dir=path.parent, delete=False) as stream:
            name = stream.name
            stream.write(text)
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


class Worker:
    def __init__(self, cfg: Config, *, downloader=None, tagger=None, fake=False, local=False):
        if cfg.role != 'server' and not local:
            raise ValueError('the worker requires role = "server" (or explicit sync --local)')
        self.cfg = cfg
        if downloader is not None:
            self.downloader = downloader
        elif fake:
            self.downloader = lambda id, song, dst: download.fake_download(id, song, dst, cfg.fake_delay)
        else:
            self.downloader = download.download
        self.tagger = tagger or ((lambda path, song: None) if fake else download.tag)
        self.state_file = _state_dir(cfg) / 'state.json'
        self.state = status.read(self.state_file) or {}
        old_status = status.read(cfg.status_file) or {}
        self.failed = dict(old_status.get('failed', {}))
        self.last_status = 0.0
        self.local = local
        self.fake = fake

    def signature(self):
        paths = [self.cfg.library_dir / LIBRARY_FILE,
                 *self.cfg.library_dir.glob(CONFLICT_GLOB), self.cfg.songs_dir]
        sig = []
        for path in paths:
            try:
                st = path.stat()
                sig.append((path.name, st.st_mtime_ns, st.st_size))
            except FileNotFoundError:
                pass
        return tuple(sorted(sig))

    def _publish(self, lib=None, *, blocked=None, downloading=None, pending_trash=0):
        live = lib.live() if lib else {}
        files = scan(self.cfg.songs_dir)
        have = {file_id(name) for name in files}
        self.failed = {id: err for id, err in self.failed.items() if id in live}
        status.write(self.cfg.status_file, {
            'server': self.cfg.device,
            'seen': time.time(),
            'songs': len(live),
            'files': len(files),
            'pending': sorted(id for id in live if id not in have and id not in self.failed and id != downloading),
            'downloading': [downloading] if downloading else [],
            'failed': self.failed,
            'blocked': blocked,
            'pending_trash': pending_trash,
            'yt_dlp': download.version(),
            'canary': self.state.get('canary'),
            'inbox': self.state.get('inbox'),
            'local': self.local,
        })
        self.last_status = time.time()

    def _inbox(self, lib, log):
        url = getattr(self.cfg, 'inbox_playlist', None)
        if not url or time.time() - self.state.get('inbox', {}).get('checked', 0) < self.cfg.inbox_interval:
            return
        info = {'checked': time.time(), 'error': None, 'added': 0}
        try:
            from .sources import Source
            for id, fields in Source().playlist(url):
                # Tombstones are deliberately remembered, so the inbox cannot resurrect removals.
                if id not in lib.songs:
                    lib.put(id, **fields)
                    info['added'] += 1
            if info['added']:
                lib.save()
                log.append(f'imported {info["added"]} songs from inbox')
        except Exception as exc:
            info['error'] = str(exc)
            log.append(f'inbox failed: {exc}')
        self.state['inbox'] = info

    def _canary(self, log):
        id = getattr(self.cfg, 'canary_id', None)
        if not id or time.time() - self.state.get('canary', {}).get('checked', 0) < self.cfg.canary_interval:
            return
        info = {'checked': time.time(), 'id': id, 'ok': False, 'error': None}
        path = _state_dir(self.cfg) / 'canary.mp3'
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.downloader(id, {'title': 'ytmm canary', 'artists': []}, path)
            info['ok'] = True
            log.append('canary OK')
        except Exception as exc:
            info['error'] = str(exc)
            log.append(f'canary failed: {exc}')
            url = getattr(self.cfg, 'notify_url', None)
            if url:
                try:
                    request = Request(url, data=f'ytmm canary failed: {exc}'.encode(), method='POST', headers={'Title': 'ytmm download failure'})
                    with urlopen(request, timeout=15):
                        pass
                except Exception as notify_exc:
                    info['notification_error'] = str(notify_exc)
        finally:
            path.unlink(missing_ok=True)
        self.state['canary'] = info

    def _tagged(self, songs):
        tagged = {}
        for id, saved in self.state.get('tagged', {}).items():
            if id not in songs or songs[id].get('deleted'):
                continue
            path = self.cfg.songs_dir / file_name(id, songs[id])
            try:
                stat = path.stat()
                if [stat.st_size, stat.st_mtime_ns] == saved.get('stat'):
                    tagged[id] = saved.get('key')
            except FileNotFoundError:
                pass
        return tagged

    def _remember(self, id, song):
        path = self.cfg.songs_dir / file_name(id, song)
        stat = path.stat()
        self.state.setdefault('tagged', {})[id] = {'key': metadata_key(song), 'stat': [stat.st_size, stat.st_mtime_ns]}

    def _playlists(self, lib):
        groups = {}
        for id, song in lib.live().items():
            name = file_name(id, song)
            if not (self.cfg.songs_dir / name).is_file():
                continue
            for tag in song.get('tags', []):
                groups.setdefault(tag, []).append((name, song))
        directory = self.cfg.music_dir / 'playlists'
        generated = []
        for tag, items in sorted(groups.items()):
            label = re_unsafe.sub('', tag).strip(' .').encode()[:120].decode(errors='ignore') or 'tag'
            name = f'{label}-{hashlib.sha256(tag.encode()).hexdigest()[:8]}.m3u'
            lines = ['#EXTM3U']
            for filename, song in sorted(items):
                title = f'{", ".join(song.get("artists", []))} - {song["title"]}'.replace('\n', ' ').replace('\r', ' ')
                lines += [f'#EXTINF:-1,{title}', f'../songs/{filename}']
            _atomic_text(directory / name, '\n'.join(lines) + '\n')
            generated.append(name)
        for name in self.state.get('playlists', []):
            if name not in generated and Path(name).name == name:
                (directory / name).unlink(missing_ok=True)
        self.state['playlists'] = generated

    def run_once(self) -> list[str]:
        with worker_lock(self.cfg):
            return self._run_once()

    def _run_once(self) -> list[str]:
        cfg, log = self.cfg, []
        cfg.library_dir.mkdir(parents=True, exist_ok=True)
        cfg.songs_dir.mkdir(parents=True, exist_ok=True)
        path = cfg.library_dir / LIBRARY_FILE
        try:
            if not path.exists():
                raise ValueError('library.json is missing; waiting for it to sync (no files changed)')
            lib = Library(cfg.library_dir, cfg.device).load()
            if list(cfg.library_dir.glob(CONFLICT_GLOB)):
                raise ValueError('unreadable library conflict copies need attention (no files changed)')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self._publish(blocked=str(exc))
            return [f'blocked: {exc}']
        log.extend(f'merged {copy.name}' for copy in lib.merged_conflicts)
        self._inbox(lib, log)
        actions = plan(lib.songs, scan(cfg.songs_dir), self._tagged(lib.songs))
        trash = [a for a in actions if a.kind == 'trash']
        blocked = None
        if len(trash) > cfg.max_trash:
            blocked = f'refused to trash {len(trash)} files (limit {cfg.max_trash})'
            log.append(blocked)
            actions = [a for a in actions if a.kind != 'trash']
        pass_failures = set()
        for action in actions:
            try:
                if action.kind == 'trash':
                    # Compare the exact planned library, rather than a timestamp
                    # captured after inbox requests or other concurrent edits.
                    # Hold the local writer lock through the move as well.
                    with library_lock(path):
                        try:
                            unchanged = read(path) == lib.songs and not list(cfg.library_dir.glob(CONFLICT_GLOB))
                        except (OSError, ValueError):
                            unchanged = False
                        if not unchanged:
                            blocked = 'library changed during reconciliation; postponing trash'
                            continue
                        move_to_trash(cfg.songs_dir / action.src, cfg.trash)
                    log.append(f'trashed {action.src} ({action.why})')
                elif action.kind == 'rename':
                    rename_song(cfg.songs_dir / action.src, cfg.songs_dir / action.dst)
                    log.append(f'renamed {action.src} -> {action.dst}')
                elif action.kind == 'retag':
                    if action.id in pass_failures:
                        continue
                    self.tagger(cfg.songs_dir / action.src, lib.songs[action.id])
                    self._remember(action.id, lib.songs[action.id])
                    self.failed.pop(action.id, None)
                    log.append(f'tagged {action.src}')
                elif action.kind == 'download':
                    self._publish(lib, blocked=blocked, downloading=action.id, pending_trash=len(trash) if blocked else 0)
                    self.downloader(action.id, lib.songs[action.id], cfg.songs_dir / action.dst)
                    self._remember(action.id, lib.songs[action.id])
                    self.failed.pop(action.id, None)
                    log.append(f'downloaded {action.dst}')
            except Exception as exc:
                if action.id:
                    pass_failures.add(action.id)
                    self.failed[action.id] = str(exc)
                else:
                    blocked = f'{action.kind} failed: {exc}'
                log.append(f'failed {action.id or action.src}: {exc}')
        self._playlists(lib)
        self._canary(log)
        self.state['tagged'] = {id: saved for id, saved in self.state.get('tagged', {}).items() if id in lib.live()}
        status.write(self.state_file, self.state)
        self._publish(lib, blocked=blocked, pending_trash=len(trash) if blocked else 0)
        purge_trash(cfg.trash)
        return log

    def run(self, on_log=print) -> None:
        last_sig = None
        with worker_lock(self.cfg):
            while True:
                signature = self.signature()
                now = time.time()
                due = (getattr(self.cfg, 'inbox_playlist', None) and now - self.state.get('inbox', {}).get('checked', 0) >= self.cfg.inbox_interval
                       or getattr(self.cfg, 'canary_id', None) and now - self.state.get('canary', {}).get('checked', 0) >= self.cfg.canary_interval)
                if signature != last_sig or now - self.last_status >= HEARTBEAT or due:
                    try:
                        for line in self._run_once():
                            on_log(line)
                    except Exception as exc:
                        logger.exception('worker pass failed')
                        self._publish(blocked=f'worker pass failed: {exc}')
                        on_log(f'worker pass failed: {exc}')
                    # A concurrent library edit must trigger the next pass too.
                    last_sig = signature
                time.sleep(self.cfg.poll_interval)
