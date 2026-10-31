"""
library.json (shared through Syncthing):
{
  "version": 2,
  "songs": {
    "<video id>": {"title": str, "artists": [str], "album": str?, "updated": float, "updated_by": str},
    "<video id>": {"deleted": true, "updated": float, "updated_by": str}    <- tombstone
  }
}

Every device may edit this file, so Syncthing will sometimes leave
`library.sync-conflict-*.json` copies. `load` merges them into library.json:
for each song the newest edit wins. Ties are broken by device name and then by
content, so every device computes the same result and writes identical bytes.
"""
import json, logging, os, re, time
from pathlib import Path

VERSION       = 2
LIBRARY_FILE  = 'library.json'
CONFLICT_GLOB = 'library.sync-conflict-*.json'

re_video_id = re.compile(r'^[0-9A-Za-z_-]{11}$')
re_url_id   = re.compile(r'(?:v=|youtu\.be/|/shorts/|/embed/)([0-9A-Za-z_-]{11})')

logger = logging.getLogger(__name__)


def parse_id(s: str) -> str | None:
    s = s.strip()
    if re_video_id.match(s):
        return s
    m = re_url_id.search(s)
    return m.group(1) if m else None


def _key(entry: dict):
    return (entry.get('updated', 0), entry.get('updated_by', ''), json.dumps(entry, sort_keys=True))


def merge(*song_maps: dict) -> dict:
    merged = {}
    for songs in song_maps:
        for id, entry in songs.items():
            if id not in merged or _key(entry) > _key(merged[id]):
                merged[id] = entry
    return merged


def read(path: Path) -> dict:
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    if data.get('version') != VERSION:
        raise ValueError(f'"{path}" is not a v{VERSION} library')
    return data['songs']


def write(path: Path, songs: dict) -> None:
    text = json.dumps({'version': VERSION, 'songs': songs}, indent=2, sort_keys=True, ensure_ascii=False)
    tmp = path.with_name(f'.{path.name}.tmp')  # ignored through .stignore
    tmp.write_text(text + '\n', encoding='utf-8')
    os.replace(tmp, path)


class Library:
    def __init__(self, directory: Path, device: str):
        self.path   = directory / LIBRARY_FILE
        self.device = device
        self.songs  = {}
        self.merged_conflicts = []

    def load(self) -> 'Library':
        self.songs = read(self.path) if self.path.exists() else {}

        conflicts = sorted(self.path.parent.glob(CONFLICT_GLOB))
        maps = [self.songs]
        for c in conflicts:
            try:
                maps.append(read(c))
                self.merged_conflicts.append(c)
            except (OSError, ValueError) as e:
                logger.warning('skipping unreadable conflict copy "%s": %s', c, e)

        if self.merged_conflicts:
            self.songs = merge(*maps)
            self.save()
            for c in self.merged_conflicts:
                c.unlink(missing_ok=True)
        return self

    def save(self) -> None:
        write(self.path, self.songs)

    def live(self) -> dict:
        return {id: s for id, s in self.songs.items() if not s.get('deleted')}

    def put(self, id: str, **fields) -> dict:
        entry = dict(self.songs.get(id, {}))
        entry.pop('deleted', None)
        entry.update({k: v for k, v in fields.items() if v is not None})
        entry.setdefault('title', id)
        entry.setdefault('artists', [])
        entry['updated']    = time.time()
        entry['updated_by'] = self.device
        self.songs[id] = entry
        return entry

    def delete(self, id: str) -> None:
        self.songs[id] = {'deleted': True, 'updated': time.time(), 'updated_by': self.device}
