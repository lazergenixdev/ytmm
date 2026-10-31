"""Per-device settings. No machine-specific paths belong in the shared library."""
import hashlib
import json
import math
import os
import socket
import tempfile
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_CONFIG = '~/.config/ytmm/config.toml'
DEFAULT_TRASH = '~/.local/share/ytmm/trash'
ROLES = ('client', 'server')


def config_path() -> Path:
    # Allows isolated installations and testing several devices on one machine.
    return Path(os.environ.get('YTMM_CONFIG', DEFAULT_CONFIG)).expanduser()


def _inside(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


@dataclass
class Config:
    root: Path
    role: str = 'client'
    device: str = field(default_factory=socket.gethostname)
    trash: Path = field(default_factory=lambda: Path(DEFAULT_TRASH).expanduser())
    poll_interval: float = 5.0
    max_trash: int = 10
    fake_delay: float = 1.0  # Used only by the explicitly selected fake MVP worker.
    inbox_playlist: str | None = None
    inbox_interval: float = 300.0
    canary_id: str | None = None
    canary_interval: float = 86400.0
    notify_url: str | None = None

    def __post_init__(self):
        if not isinstance(self.root, (str, Path)) or not str(self.root).strip():
            raise ValueError('root must be a filesystem path')
        if not isinstance(self.trash, (str, Path)) or not str(self.trash).strip():
            raise ValueError('trash must be a filesystem path')
        self.root = Path(self.root).expanduser().resolve()
        self.trash = Path(self.trash).expanduser().resolve()
        if self.role not in ROLES:
            raise ValueError(f'role must be one of {ROLES}')
        if not isinstance(self.device, str) or not self.device.strip():
            raise ValueError('device must be a nonempty name')
        for name in ('poll_interval', 'inbox_interval', 'canary_interval'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be a finite positive number')
        if isinstance(self.max_trash, bool) or not isinstance(self.max_trash, int) or self.max_trash < 0:
            raise ValueError('max_trash must be a nonnegative integer')
        if isinstance(self.fake_delay, bool) or not isinstance(self.fake_delay, (int, float)) or not math.isfinite(self.fake_delay) or self.fake_delay < 0:
            raise ValueError('fake_delay must be a finite nonnegative number')
        for name in ('inbox_playlist', 'canary_id', 'notify_url'):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f'{name} must be a string')
        if self.canary_id:
            from .library import parse_id
            video_id = parse_id(self.canary_id)
            if not video_id:
                raise ValueError('canary_id must be a YouTube video ID or URL')
            self.canary_id = video_id
        if self.notify_url:
            from urllib.parse import urlsplit
            url = urlsplit(self.notify_url)
            if url.scheme not in ('http', 'https') or not url.netloc:
                raise ValueError('notify_url must be an http or https URL')
        if (_inside(self.trash, self.library_dir) or _inside(self.trash, self.music_dir)
                or _inside(self.root, self.trash)):
            raise ValueError('trash must be outside the synced library/music folders and cannot contain the library root')

    @property
    def library_dir(self) -> Path:
        return self.root / 'library'

    @property
    def music_dir(self) -> Path:
        return self.root / 'music'

    @property
    def songs_dir(self) -> Path:
        return self.music_dir / 'songs'

    @property
    def status_file(self) -> Path:
        return self.library_dir / 'status.json'

    @property
    def state_dir(self) -> Path:
        # Private worker bookkeeping must not cause Syncthing conflicts.
        key = hashlib.sha256(str(self.root).encode('utf-8')).hexdigest()[:12]
        return self.trash.parent / f'.ytmm-state-{key}'


def load(path: Path | None = None) -> Config:
    path = Path(path) if path is not None else config_path()
    if not path.exists():
        raise FileNotFoundError(f'no config at "{path}" (run `ytmm init` first)')
    with path.open('rb') as stream:
        data = tomllib.load(stream)
    if 'root' not in data:
        raise ValueError(f'config "{path}" is missing root')
    unknown = set(data) - {item.name for item in fields(Config)}
    if unknown:
        raise ValueError(f'unknown config settings: {", ".join(sorted(unknown))}')
    # A relative path is relative to this config, never the shell's directory.
    for name in ('root', 'trash'):
        if name in data:
            value = data[name]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{name} must be a filesystem path')
            candidate = Path(value).expanduser()
            data[name] = candidate if candidate.is_absolute() else path.resolve().parent / candidate
    return Config(**data)


def save(cfg: Config, path: Path | None = None) -> Path:
    cfg.__post_init__()
    path = Path(path) if path is not None else config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for item in fields(cfg):
        value = getattr(cfg, item.name)
        if value is None:
            continue
        encoded = json.dumps(str(value), ensure_ascii=False) if isinstance(value, (str, Path)) else str(value)
        lines.append(f'{item.name} = {encoded}')
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write('\n'.join(lines) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path
