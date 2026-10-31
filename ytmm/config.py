import os, socket, tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG = '~/.config/ytmm/config.toml'
DEFAULT_TRASH  = '~/.local/share/ytmm/trash'
ROLES = ('client', 'server')


def config_path() -> Path:
    # YTMM_CONFIG lets you run several "devices" on one machine
    return Path(os.environ.get('YTMM_CONFIG', DEFAULT_CONFIG)).expanduser()


@dataclass
class Config:
    root:          Path
    role:          str   = 'client'
    device:        str   = field(default_factory=socket.gethostname)
    trash:         Path  = Path(DEFAULT_TRASH).expanduser()
    poll_interval: float = 5.0   # seconds between checks for library changes
    max_trash:     int   = 10    # refuse plans that trash more files than this
    fake_delay:    float = 1.0   # seconds the fake downloader "takes" per song

    @property
    def library_dir(self) -> Path: return self.root / 'library'
    @property
    def music_dir(self)   -> Path: return self.root / 'music'
    @property
    def songs_dir(self)   -> Path: return self.music_dir / 'songs'
    @property
    def status_file(self) -> Path: return self.library_dir / 'status.json'


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        raise FileNotFoundError(f'no config at "{path}" (run `ytmm-mvp init` first)')
    with open(path, 'rb') as f:
        data = tomllib.load(f)
    data['root'] = Path(data['root']).expanduser()
    if 'trash' in data:
        data['trash'] = Path(data['trash']).expanduser()
    if data.get('role', 'client') not in ROLES:
        raise ValueError(f'role must be one of {ROLES}')
    return Config(**data)


def save(cfg: Config, path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    def q(s): return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"') + '"'
    lines = [
        f'root = {q(cfg.root)}',
        f'role = {q(cfg.role)}',
        f'device = {q(cfg.device)}',
        f'trash = {q(cfg.trash)}',
        f'poll_interval = {cfg.poll_interval}',
        f'max_trash = {cfg.max_trash}',
        f'fake_delay = {cfg.fake_delay}',
    ]
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path
