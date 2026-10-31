"""
Server loop: watch library.json, merge conflict copies, plan, apply, write
status.json. It also checks the inbox playlist, runs the daily update command
and canary, and sends a push notification when something breaks.
"""
import hashlib, logging, os, shutil, subprocess, time, urllib.request
from pathlib import Path
from . import __version__, status
from .config import Config
from .library import Library, LibraryError, LIBRARY_FILE, CONFLICT_GLOB, describe
from .plan import plan, playlists, file_id, file_name, tag_hash, PLAYLIST_EXT

HEARTBEAT    = 300     # rewrite status.json at least this often (seconds)
STATUS_EVERY = 10      # while working, rewrite it at most this often
UPDATE_EVERY = 86400
TRASH_DAYS   = 30
RETRY_FIRST  = 300     # retry a failed download after 5 min, doubling up to once a day
RETRY_MAX    = 86400
ERROR_PAUSE  = 60      # after an unexpected error, wait this long before trying again

logger = logging.getLogger(__name__)


def scan(songs_dir: Path) -> list[str]:
    if not songs_dir.is_dir():
        return []
    return [e.name for e in os.scandir(songs_dir) if e.is_file() and not e.name.startswith('.')]


def move_to_trash(src: Path, trash_dir: Path) -> Path:
    trash_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    dst = trash_dir / f'{stamp} {src.name}'  # still ends in "[id].ext", so it can be found again
    n = 1
    while dst.exists():
        dst = trash_dir / f'{stamp}-{n} {src.name}'
        n += 1
    shutil.move(src, dst)
    os.utime(dst)  # purge age counts from when it was trashed
    return dst


def find_in_trash(trash_dir: Path, id: str) -> Path | None:
    if not trash_dir.is_dir():
        return None
    found = [Path(e.path) for e in os.scandir(trash_dir) if e.is_file() and file_id(e.name) == id]
    return max(found, key=lambda p: p.stat().st_mtime, default=None)


def purge_trash(trash_dir: Path, days: int = TRASH_DAYS) -> None:
    if not trash_dir.exists():
        return
    cutoff = time.time() - days * 86400
    for e in os.scandir(trash_dir):
        if e.is_file() and e.stat().st_mtime < cutoff:
            os.remove(e.path)


def send_notification(url: str, title: str, message: str) -> None:
    """POST to an ntfy topic URL (https://ntfy.sh/<topic>, or a self-hosted one)."""
    if not url:
        return
    headers = {'Title': title.encode('ascii', 'replace').decode(), 'Tags': 'musical_note'}
    try:
        urllib.request.urlopen(urllib.request.Request(url, message.encode('utf-8'), headers), timeout=15).close()
    except (OSError, ValueError) as e:
        logger.warning('could not send notification: %s', e)


def short(e: Exception) -> str:
    lines = str(e).strip().splitlines()
    return (lines[-1] if lines else type(e).__name__)[:300]


class State:
    """
    Bookkeeping kept on the server, outside the synced folders:
    tagged: {id: tag_hash written into its file}, failed: {id: retry info},
    and the last inbox check, canary and update.
    """
    def __init__(self, path: Path):
        self.path = path
        self.data = status.read(path) or {}
        for key in ('tagged', 'failed'):
            self.data.setdefault(key, {})

    def __getitem__(self, key: str):
        return self.data[key]

    def get(self, key: str) -> dict:
        return self.data.get(key) or {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        status.write(self.path, self.data)


class Worker:
    def __init__(self, cfg: Config, downloader=None, on_log=None, write_status=True, fetch_inbox=None, notify=None):
        self.cfg = cfg
        self.downloader = downloader
        self.on_log = on_log or (lambda line: None)
        self.write_status = write_status  # False for `sync --local`: status.json is the server's
        self.fetch_inbox = fetch_inbox
        self.notify = notify or (lambda title, message: send_notification(cfg.notify, title, message))
        self.state = State(cfg.state_file)
        self.lines = []
        self.blocked = None
        self.error = None
        self.downloading = None
        self.ytdlp = None
        self.last_status = 0.0
        self.lib_sig = None  # library.json as this pass planned with

    @property
    def dl(self):
        if self.downloader is None:
            from .download import make
            self.downloader = make(self.cfg)
        return self.downloader

    def _log(self, line: str) -> None:
        self.lines.append(line)
        self.on_log(line)

    def library_signature(self):
        """Changes whenever library.json, a conflict copy or approve.json changes."""
        lib = self.cfg.library_dir
        sig = []
        for p in [lib / LIBRARY_FILE, self.cfg.approve_file, *lib.glob(CONFLICT_GLOB)]:
            try:
                st = p.stat()
                sig.append((p.name, st.st_mtime_ns, st.st_size))
            except FileNotFoundError:
                pass
        return tuple(sorted(sig))

    def signature(self):
        """Also changes when music/songs does."""
        sig = list(self.library_signature())
        try:
            sig.append(('songs/', self.cfg.songs_dir.stat().st_mtime_ns))
        except FileNotFoundError:
            pass
        return tuple(sig)

    #
    # One pass
    #

    def run_once(self) -> list[str]:
        cfg = self.cfg
        self.lines = []
        cfg.songs_dir.mkdir(parents=True, exist_ok=True)
        try:
            lib = Library(cfg.library_dir, cfg.device).load()
        except (OSError, LibraryError) as e:
            if self.error != str(e):
                self.notify('ytmm: library.json is unreadable', str(e))
            self.error = str(e)
            self._log(f'not syncing: {e}')
            self._write_status(None)
            return self.lines
        self.error = None
        for c in lib.merged_conflicts:
            self._log(f'merged {c.name}')

        actions = plan(lib.songs, scan(cfg.songs_dir), self.state['tagged'], ext=f'.{cfg.format}')
        trash = [a for a in actions if a.kind == 'trash']
        prev_blocked, self.blocked = self.blocked, None
        if trash and not self._trash_allowed(lib, trash, prev_blocked):
            actions = [a for a in actions if a.kind != 'trash']
        elif cfg.approve_file.exists():  # approval for a plan that no longer exists
            cfg.approve_file.unlink(missing_ok=True)

        self.lib_sig = self.library_signature()
        for a in actions:
            if a.kind in ('retag', 'download') and self.library_signature() != self.lib_sig:
                self._log('library changed, planning again')
                break
            self._write_status(lib, throttle=True)
            try:
                self._apply(a, lib)
            except Exception as e:
                if a.kind == 'download':
                    self._failed(a.id, lib.songs[a.id], e)
                else:
                    self._log(f'could not {a.kind} {a.src}: {short(e)}')
            finally:
                self.downloading = None

        live, have = lib.live(), {file_id(f) for f in scan(cfg.songs_dir)}
        self.state.data['failed'] = {id: f for id, f in self.state['failed'].items() if id in live and id not in have}
        self.state.data['tagged'] = {id: h for id, h in self.state['tagged'].items() if id in have}
        self.state.save()
        self._playlists(lib)
        self._write_status(lib)
        purge_trash(cfg.trash)
        return self.lines

    def _trash_allowed(self, lib: Library, trash: list, prev_blocked: dict | None) -> bool:
        files = [a.src for a in trash]
        if not lib.path.exists():
            self.blocked = {'count': len(trash), 'limit': 0, 'token': None, 'reason': 'library.json is missing', 'files': files[:20]}
            if not prev_blocked:
                self._log(f'not trashing {len(trash)} files: library.json is missing')
            return False
        limit = max(self.cfg.max_trash, int(len(lib.live()) * self.cfg.max_trash_percent / 100))
        if len(trash) <= limit:
            return True
        token = hashlib.sha1('\n'.join(sorted(files)).encode('utf-8')).hexdigest()[:12]
        approval = status.read(self.cfg.approve_file) or {}
        self.cfg.approve_file.unlink(missing_ok=True)  # used now, or stale
        if approval.get('token') == token:
            self._log(f'trashing {len(trash)} files, approved by {approval.get("by", "?")}')
            return True
        self.blocked = {'count': len(trash), 'limit': limit, 'token': token, 'reason': 'too many files to trash', 'files': files[:20]}
        if not prev_blocked or prev_blocked.get('token') != token:
            self._log(f'refused to trash {len(trash)} files (limit {limit}); `ytmm approve` allows it')
            self.notify('ytmm: refused to delete songs',
                        f'A sync would trash {len(trash)} files (limit {limit}). If that is right, run `ytmm approve`.')
        return False

    def _apply(self, a, lib: Library) -> None:
        cfg = self.cfg
        if a.kind == 'trash':
            move_to_trash(cfg.songs_dir / a.src, cfg.trash)
            self.state['tagged'].pop(a.id, None)
            self._log(f'trashed {a.src} ({a.why})')
        elif a.kind == 'rename':
            os.replace(cfg.songs_dir / a.src, cfg.songs_dir / a.dst)
            self._log(f'renamed {a.src} -> {a.dst}')
        elif a.kind == 'retag':
            song = lib.songs[a.id]
            tmp = cfg.tmp_dir / a.src  # tag a copy, so Syncthing never sees a half-written file
            tmp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cfg.songs_dir / a.src, tmp)
            try:
                self.dl.tag(tmp, a.id, song)
                self._move_in(tmp, a.src)
            finally:
                tmp.unlink(missing_ok=True)
            self.state['tagged'][a.id] = tag_hash(a.id, song)
            self.state.save()
            self._log(f'tagged {a.src}')
        elif a.kind == 'download':
            song = lib.songs[a.id]
            failed = self.state['failed'].get(a.id)
            if failed and failed.get('updated') == song.get('updated') and failed['retry'] > time.time():
                return  # waiting to retry
            self.downloading = a.id
            self._write_status(lib, throttle=True)
            self._log(self._download(a.id, song))
            self.state['failed'].pop(a.id, None)
            self.state.save()

    def _download(self, id: str, song: dict) -> str:
        cfg = self.cfg
        tmp = cfg.tmp_dir / id
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        try:
            old = find_in_trash(cfg.trash, id)
            if old:  # the song was restored: no need to download it again
                path = tmp / f'{id}{old.suffix}'
                shutil.move(old, path)
                how = 'restored from trash'
            else:
                path, info = self.dl.download(id, song, tmp)
                how = 'downloaded'
                if not song.get('title') and info:
                    song = self._fill_metadata(id, info) or song
            try:
                self.dl.tag(path, id, song)
            except Exception as e:
                self._log(f'could not tag {id}: {short(e)}')
            name = file_name(id, song, path.suffix.lower())
            self._move_in(path, name)
            self.state['tagged'][id] = tag_hash(id, song)
            return f'{how} {name}'
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _move_in(self, src: Path, name: str) -> None:
        """Put a finished file into music/songs in one step."""
        part = self.cfg.songs_dir / f'.{name}.part'  # ignored through .stignore until it's complete
        shutil.move(src, part)
        os.replace(part, self.cfg.songs_dir / name)

    def _failed(self, id: str, song: dict, e: Exception) -> None:
        prev = self.state['failed'].get(id)
        attempts = prev['attempts'] + 1 if prev and prev.get('updated') == song.get('updated') else 1
        delay = min(RETRY_FIRST * 2 ** (attempts - 1), RETRY_MAX)
        self.state['failed'][id] = {'error': short(e), 'attempts': attempts, 'retry': time.time() + delay,
                                    'updated': song.get('updated')}
        self.state.save()
        self._log(f'failed {id}: {short(e)} (retry in {delay // 60} min)')

    def _fill_metadata(self, id: str, info: dict) -> dict | None:
        """The song was added without metadata (offline): take it from yt-dlp."""
        from .download import meta_from_info
        meta = meta_from_info(info)
        if not meta:
            return None
        changed_meanwhile = self.library_signature() != self.lib_sig
        try:
            lib = Library(self.cfg.library_dir, self.cfg.device).load()  # fresh: a download takes a while
        except (OSError, LibraryError) as e:
            self._log(f'could not save metadata for {id}: {e}')
            return None
        song = lib.songs.get(id)
        if not song or song.get('deleted') or song.get('title'):
            return song
        lib.put(id, **meta)
        lib.save()
        if not changed_meanwhile:  # our own write is no reason to plan again
            self.lib_sig = self.library_signature()
        self._log(f'filled in metadata: {describe(id, lib.songs[id])}')
        return lib.songs[id]

    def _playlists(self, lib: Library) -> None:
        d = self.cfg.playlists_dir
        names = {file_id(f): f for f in scan(self.cfg.songs_dir)}
        want = playlists(lib.songs, names)
        if not want and not d.exists():
            return
        d.mkdir(parents=True, exist_ok=True)
        for name, text in want.items():
            p = d / name
            try:
                if p.read_text(encoding='utf-8') == text:
                    continue
            except OSError:
                pass
            tmp = d / f'.{name}.tmp'
            tmp.write_text(text, encoding='utf-8')
            os.replace(tmp, p)
            self._log(f'wrote playlists/{name}')
        for e in os.scandir(d):
            if e.is_file() and e.name.endswith(PLAYLIST_EXT) and e.name not in want:
                os.remove(e.path)
                self._log(f'removed playlists/{e.name}')

    def _write_status(self, lib: Library | None, throttle: bool = False) -> None:
        if not self.write_status or (throttle and time.time() - self.last_status < STATUS_EVERY):
            return
        live = lib.live() if lib else {}
        files = scan(self.cfg.songs_dir)
        have = {file_id(f) for f in files}
        failed = {id: {k: f.get(k) for k in ('error', 'attempts', 'retry')}
                  for id, f in self.state['failed'].items() if id in live and id not in have}
        inbox, update = self.state.get('inbox'), self.state.get('update')
        status.write(self.cfg.status_file, {
            'server':      self.cfg.device,
            'seen':        time.time(),
            'version':     __version__,
            'ytdlp':       self.ytdlp,
            'songs':       len(live),
            'files':       len(files),
            'downloading': self.downloading,
            'pending':     sorted(id for id in live if id not in have and id not in failed),
            'failed':      failed,
            'blocked':     self.blocked,
            'canary':      self.state.data.get('canary'),
            'inbox':       {k: v for k, v in inbox.items() if k != 'ids'} or None,
            'update':      {k: v for k, v in update.items() if k != 'output'} or None,
            'error':       self.error,
        })
        self.last_status = time.time()

    #
    # Timers
    #

    def check_inbox(self) -> list[str]:
        """Import songs newly added to the inbox playlist. Returns their ids."""
        from . import sources
        prev, now = self.state.get('inbox'), time.time()
        try:
            tracks = (self.fetch_inbox or sources.inbox_tracks)(self.cfg.inbox)
        except Exception as e:
            self.state.data['inbox'] = {**prev, 'time': now, 'ok': False, 'error': short(e)}
            self.state.save()
            self._log(f'inbox check failed: {short(e)}')
            return []
        if not tracks and prev.get('ids'):
            # an empty answer for a playlist that had songs is more likely a glitch than the truth
            self.state.data['inbox'] = {**prev, 'time': now}
            self.state.save()
            return []
        imported = []
        try:
            lib = Library(self.cfg.library_dir, self.cfg.device).load()
            new = sources.inbox_new(lib.songs, prev.get('ids'), tracks)
            if new:
                sources.complete(new)  # album years: this takes a moment, so reload afterwards
                lib = Library(self.cfg.library_dir, self.cfg.device).load()
                for t in sources.inbox_new(lib.songs, prev.get('ids'), new):
                    lib.put(t.id, added=time.time(), **t.fields())
                    imported.append(t)
                if imported:
                    lib.save()
        except (OSError, LibraryError) as e:
            self._log(f'inbox not imported: {e}')
            return []
        self.state.data['inbox'] = {'time': now, 'ok': True, 'error': None, 'ids': [t.id for t in tracks],
                                    'imported': len(imported), 'last_import': now if imported else prev.get('last_import')}
        self.state.save()
        for t in imported:
            self._log(f'imported from inbox: {describe(t.id, t.fields())}')
        return [t.id for t in imported]

    def run_canary(self, record: bool = True) -> bool:
        """Download a known video, to find out early when YouTube breaks yt-dlp."""
        tmp = self.cfg.tmp_dir / 'canary'
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        try:
            path, _ = self.dl.download(self.cfg.canary, {}, tmp)
            ok, error = path.stat().st_size > 0, None
            if not ok:
                error = 'the downloaded file is empty'
        except Exception as e:
            ok, error = False, short(e)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.ytdlp = self.dl.version()
        self._log(f'canary {"OK" if ok else "FAILED: " + error} (yt-dlp {self.ytdlp})')
        if record:
            prev = self.state.get('canary')
            self.state.data['canary'] = {'time': time.time(), 'ok': ok, 'error': error, 'ytdlp': self.ytdlp}
            if ok and prev.get('ok') is False:
                for f in self.state['failed'].values():  # those failures were probably the same breakage
                    f['retry'] = 0
                self.notify('ytmm: downloads work again', f'The canary download works again (yt-dlp {self.ytdlp}).')
            elif not ok and prev.get('ok', True):
                self.notify('ytmm: downloads are broken', f'The canary download failed: {error}\n(yt-dlp {self.ytdlp})')
            self.state.save()
            self.last_status = 0  # show it in status.json right away
        return ok

    def run_update(self) -> bool:
        """Run the update command (e.g. `uv tool upgrade ytmm`): yt-dlp needs frequent updates."""
        cmd = self.cfg.update_command
        self._log(f'running update command: {cmd}')
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=1800)
            ok, out = r.returncode == 0, (r.stdout + r.stderr).strip()
        except (OSError, subprocess.SubprocessError) as e:
            ok, out = False, str(e)
        self.state.data['update'] = {'time': time.time(), 'ok': ok, 'output': out[-2000:]}
        self.state.save()
        self.ytdlp = self.dl.version()
        if ok:
            self._log(f'update OK (yt-dlp {self.ytdlp})')
        else:
            self._log(f'update failed: {out[-300:]}')
            self.notify('ytmm: update failed', out[-500:] or 'the update command failed')
        return ok

    def _retry_due(self) -> bool:
        now = time.time()
        return any(f.get('retry', 0) <= now for f in self.state['failed'].values())

    def _timers(self) -> None:
        cfg, now = self.cfg, time.time()
        if cfg.update_command and now - self.state.get('update').get('time', 0) >= UPDATE_EVERY:
            self.run_update()
        if cfg.canary and now - self.state.get('canary').get('time', 0) >= cfg.canary_interval:
            self.run_canary()
        if cfg.inbox and now - self.state.get('inbox').get('time', 0) >= cfg.inbox_interval:
            self.check_inbox()

    def run(self) -> None:
        self.ytdlp = self.dl.version()
        shutil.rmtree(self.cfg.tmp_dir, ignore_errors=True)  # leftovers from an interrupted download
        last_sig = None
        while True:
            pause = self.cfg.poll_interval
            try:
                self._timers()
                sig = self.signature()
                if sig != last_sig or time.time() - self.last_status >= HEARTBEAT or self._retry_due():
                    self.run_once()
                    # our own downloads and renames change the signature; take it after the run
                    last_sig = self.signature()
            except Exception as e:  # keep the service alive through anything unexpected
                logger.exception('worker pass failed')
                self._log(f'error: {short(e)}')
                last_sig, pause = None, ERROR_PAUSE
            time.sleep(pause)
