import logging, os, shutil, time
from pathlib import Path
from . import download, status
from .config import Config
from .library import Library, LIBRARY_FILE, CONFLICT_GLOB
from .plan import plan, file_id

HEARTBEAT  = 300           # rewrite status.json at least this often (seconds)
TRASH_DAYS = 30

logger = logging.getLogger(__name__)


def scan(songs_dir: Path) -> list[str]:
    return [e.name for e in os.scandir(songs_dir) if e.is_file() and not e.name.startswith('.')]


def move_to_trash(src: Path, trash_dir: Path) -> Path:
    trash_dir.mkdir(parents=True, exist_ok=True)
    dst = trash_dir / src.name
    n = 1
    while dst.exists():
        dst = trash_dir / f'{src.stem} ({n}){src.suffix}'
        n += 1
    shutil.move(src, dst)
    os.utime(dst)  # purge age counts from when it was trashed
    return dst


def purge_trash(trash_dir: Path, days: int = TRASH_DAYS) -> None:
    if not trash_dir.exists():
        return
    cutoff = time.time() - days * 86400
    for e in os.scandir(trash_dir):
        if e.is_file() and e.stat().st_mtime < cutoff:
            os.remove(e.path)


class Worker:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.failed = {}
        self.last_status = 0.0

    def signature(self):
        """Changes whenever library.json, a conflict copy or music/songs changes."""
        lib = self.cfg.library_dir
        sig = []
        for p in [lib / LIBRARY_FILE, *lib.glob(CONFLICT_GLOB), self.cfg.songs_dir]:
            try:
                st = p.stat()
                sig.append((p.name, st.st_mtime_ns, st.st_size))
            except FileNotFoundError:
                pass
        return tuple(sorted(sig))

    def run_once(self) -> list[str]:
        cfg = self.cfg
        cfg.songs_dir.mkdir(parents=True, exist_ok=True)
        log = []

        lib = Library(cfg.library_dir, cfg.device).load()
        for c in lib.merged_conflicts:
            log.append(f'merged {c.name}')

        actions = plan(lib.songs, scan(cfg.songs_dir))
        trash = [a for a in actions if a.kind == 'trash']
        blocked = None
        if len(trash) > cfg.max_trash:
            blocked = f'refused to trash {len(trash)} files (limit {cfg.max_trash})'
            log.append(blocked)
            actions = [a for a in actions if a.kind != 'trash']

        for a in actions:
            if a.kind == 'trash':
                move_to_trash(cfg.songs_dir / a.src, cfg.trash)
                log.append(f'trashed {a.src} ({a.why})')
            elif a.kind == 'rename':
                os.replace(cfg.songs_dir / a.src, cfg.songs_dir / a.dst)
                log.append(f'renamed {a.src} -> {a.dst}')
            elif a.kind == 'download':
                try:
                    download.download(a.id, lib.songs[a.id], cfg.songs_dir / a.dst, cfg.fake_delay)
                    self.failed.pop(a.id, None)
                    log.append(f'downloaded {a.dst}')
                except Exception as e:
                    self.failed[a.id] = str(e)
                    log.append(f'failed {a.id}: {e}')

        live = lib.live()
        self.failed = {id: err for id, err in self.failed.items() if id in live}
        files = scan(cfg.songs_dir)
        have = {file_id(f) for f in files}
        status.write(cfg.status_file, {
            'server':  cfg.device,
            'seen':    time.time(),
            'songs':   len(live),
            'files':   len(files),
            'pending': sorted(id for id in live if id not in have and id not in self.failed),
            'failed':  self.failed,
            'blocked': blocked,
        })
        self.last_status = time.time()
        purge_trash(cfg.trash)
        return log

    def run(self, on_log=print) -> None:
        last_sig = None
        while True:
            sig = self.signature()
            if sig != last_sig or time.time() - self.last_status > HEARTBEAT:
                for line in self.run_once():
                    on_log(line)
                # our own downloads/renames change the signature; take it after the run
                last_sig = self.signature()
            time.sleep(self.cfg.poll_interval)
