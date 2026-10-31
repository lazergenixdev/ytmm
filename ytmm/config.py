"""
Per-device config at ~/.config/ytmm/config.toml. It is never synced: it holds
where the library lives on this device and whether this device is a `client`
or the `server`.
"""
import json, logging, os, socket, tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_CONFIG = '~/.config/ytmm/config.toml'
DEFAULT_DATA   = '~/.local/share/ytmm'
ROLES   = ('client', 'server')
FORMATS = ('mp3', 'm4a', 'opus')

logger = logging.getLogger(__name__)


def config_path() -> Path:
    # YTMM_CONFIG lets you run several "devices" on one machine
    return Path(os.environ.get('YTMM_CONFIG', DEFAULT_CONFIG)).expanduser()


@dataclass
class Config:
    root:   Path
    role:   str  = 'client'
    device: str  = field(default_factory=socket.gethostname)
    data:   Path = Path(DEFAULT_DATA).expanduser()  # server state, trash and temp files (not synced)
    trash:  Path | None = None                      # default: <data>/trash

    # server
    poll_interval:     float = 5.0     # seconds between checks for library changes
    max_trash:         int   = 10      # refuse plans that trash more files than this...
    max_trash_percent: float = 10.0    # ...or this percentage of the library, whichever is larger
    format:            str   = 'mp3'   # for new downloads: mp3, m4a or opus
    quality:           str   = '5'     # yt-dlp --audio-quality (mp3 only)
    ytdlp:             str   = ''      # command that runs yt-dlp (default: the one installed with ytmm)
    ytdlp_args:        list[str] = field(default_factory=list)
    inbox:             str   = ''      # YouTube Music playlist to import songs from
    inbox_interval:    float = 600.0
    canary:            str   = 'jNQXAC9IVRw'  # video downloaded daily to check that downloading works
    canary_interval:   float = 86400.0
    notify:            str   = ''      # ntfy topic URL for push notifications
    update_command:    str   = ''      # run daily before the canary, e.g. "uv tool upgrade ytmm"

    # testing
    downloader: str   = 'yt-dlp'  # 'fake' writes placeholder files instead of downloading
    fake_delay: float = 1.0       # seconds the fake downloader "takes" per song

    def __post_init__(self):
        if self.trash is None:
            self.trash = self.data / 'trash'

    @property
    def library_dir(self)   -> Path: return self.root / 'library'
    @property
    def music_dir(self)     -> Path: return self.root / 'music'
    @property
    def songs_dir(self)     -> Path: return self.music_dir / 'songs'
    @property
    def playlists_dir(self) -> Path: return self.music_dir / 'playlists'
    @property
    def status_file(self)   -> Path: return self.library_dir / 'status.json'
    @property
    def approve_file(self)  -> Path: return self.library_dir / 'approve.json'
    @property
    def state_file(self)    -> Path: return self.data / 'state.json'
    @property
    def tmp_dir(self)       -> Path: return self.data / 'tmp'


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        raise FileNotFoundError(f'no config at "{path}" (run `ytmm init` first)')
    with open(path, 'rb') as f:
        data = tomllib.load(f)
    known = {f.name for f in fields(Config)}
    for key in sorted(set(data) - known):
        logger.warning('ignoring unknown config key "%s" in "%s"', key, path)
    data = {k: v for k, v in data.items() if k in known}
    if 'root' not in data:
        raise ValueError(f'"{path}" does not set root')
    for key in ('root', 'data', 'trash'):
        if key in data:
            data[key] = Path(data[key]).expanduser()
    cfg = Config(**data)
    if cfg.role not in ROLES:
        raise ValueError(f'role must be one of {ROLES}')
    if cfg.format not in FORMATS:
        raise ValueError(f'format must be one of {FORMATS}')
    return cfg


def toml_value(value) -> str:
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return '[' + ', '.join(toml_value(v) for v in value) + ']'
    return json.dumps(str(value), ensure_ascii=False)  # JSON string escapes are valid TOML


SERVER_HINTS = {
    'inbox':          '"https://music.youtube.com/playlist?list=..."  # unlisted playlist to import songs from',
    'notify':         '"https://ntfy.sh/<topic>"  # push notifications when downloads break',
    'update_command': '"uv tool upgrade ytmm"  # run daily, right before the canary',
    'format':         '"mp3"  # mp3, m4a or opus (for new downloads)',
    'ytdlp_args':     '["--cookies", "/path/to/cookies.txt"]  # extra yt-dlp options',
}


def dump(cfg: Config) -> str:
    defaults = Config(root=cfg.root)
    lines = ['# ytmm device config. Not synced: every device has its own.']
    for f in fields(Config):
        value = getattr(cfg, f.name)
        if f.name == 'trash' and value == cfg.data / 'trash':
            continue
        if f.name in ('root', 'role', 'device') or value != getattr(defaults, f.name):
            lines.append(f'{f.name} = {toml_value(value)}')
    if cfg.role == 'server':
        hints = [f'# {k} = {v}' for k, v in SERVER_HINTS.items() if getattr(cfg, k) == getattr(defaults, k)]
        if hints:
            lines += ['', '# server options (uncomment to use):', *hints]
    return '\n'.join(lines) + '\n'


def save(cfg: Config, path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump(cfg), encoding='utf-8')
    return path
