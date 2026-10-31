"""
library.json (shared through Syncthing):
{
  "version": 2,
  "songs": {
    "<video id>": {
      "title": str, "artists": [str],
      "album": str?, "year": int?, "tags": [str]?,
      "cover": str?,                  square cover art URL (from YouTube Music)
      "added": float, "updated": float, "updated_by": str
    },
    "<video id>": {..., "deleted": true}    <- tombstone (keeps the metadata, so it can be restored)
  }
}

A song without a title was added without looking it up (offline); the server
fills in its metadata from yt-dlp after downloading it.

Every device may edit this file, so Syncthing will sometimes leave
`library.sync-conflict-*.json` copies. `load` merges them into library.json:
for each song the newest edit wins. Ties are broken by device name and then by
content, so every device computes the same result and writes identical bytes.
"""
import functools, json, logging, os, re, time, unicodedata
from pathlib import Path

VERSION       = 2
LIBRARY_FILE  = 'library.json'
CONFLICT_GLOB = 'library.sync-conflict-*.json'
OPTIONAL      = ('album', 'year', 'tags', 'cover')  # put(field='') removes these

re_video_id = re.compile(r'^[0-9A-Za-z_-]{11}$')
re_url_id   = re.compile(r'(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)([0-9A-Za-z_-]{11})')
re_space    = re.compile(r'\s+')
re_word     = re.compile(r'\w+')

logger = logging.getLogger(__name__)


class LibraryError(ValueError):
    pass


def parse_id(s: str) -> str | None:
    s = s.strip()
    if re_video_id.match(s):
        return s
    m = re_url_id.search(s)
    return m.group(1) if m else None


def describe(id: str, song: dict) -> str:
    title = song.get('title') or id
    artists = ', '.join(song.get('artists') or [])
    return f'{artists} - {title}' if artists else title


def normalize_tag(tag: str) -> str:
    # lowercase, so tags can't differ only by case (playlists would clash on Android and macOS)
    return re_space.sub(' ', tag).strip().casefold()


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
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except ValueError as e:
        raise LibraryError(f'"{path}" is not valid JSON ({e})') from e
    if not isinstance(data, dict) or data.get('version') != VERSION or not isinstance(data.get('songs'), dict):
        raise LibraryError(f'"{path}" is not a v{VERSION} library')
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
        if not self.path.parent.is_dir():
            raise LibraryError(f'library folder "{self.path.parent}" not found')
        self.songs = read(self.path) if self.path.exists() else {}

        conflicts = sorted(self.path.parent.glob(CONFLICT_GLOB))
        maps = [self.songs]
        for c in conflicts:
            try:
                maps.append(read(c))
                self.merged_conflicts.append(c)
            except (OSError, LibraryError) as e:
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

    def deleted(self) -> dict:
        return {id: s for id, s in self.songs.items() if s.get('deleted')}

    def tags(self) -> dict[str, int]:
        counts = {}
        for s in self.live().values():
            for tag in s.get('tags') or []:
                counts[tag] = counts.get(tag, 0) + 1
        return dict(sorted(counts.items()))

    def put(self, id: str, **fields) -> bool:
        """Add, edit or restore a song. None leaves a field alone. Returns False if nothing changed."""
        old = self.songs.get(id)
        entry = dict(old or {})
        revived = entry.pop('deleted', False)
        for k, v in fields.items():
            if v is None:
                continue
            if k in OPTIONAL and v in ('', []):
                entry.pop(k, None)
            elif k == 'tags':
                entry[k] = sorted({normalize_tag(t) for t in v} - {''})
            else:
                entry[k] = v
        entry.setdefault('artists', [])
        if old is not None and not revived and _meta(entry) == _meta(old):
            return False
        now = time.time()
        entry.setdefault('added', now)
        entry['updated']    = now
        entry['updated_by'] = self.device
        self.songs[id] = entry
        return True

    def delete(self, id: str) -> bool:
        old = self.songs.get(id)
        if old is None or old.get('deleted'):
            return False
        self.songs[id] = {**old, 'deleted': True, 'updated': time.time(), 'updated_by': self.device}
        return True


def _meta(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if k not in ('updated', 'updated_by')}


#
# Fuzzy search
#

def normalize(s: str) -> str:
    """Casefold and drop accents, so "beyonce" finds "Beyoncé"."""
    s = unicodedata.normalize('NFKD', s.casefold())
    return ''.join(c for c in s if not unicodedata.combining(c))


def _haystack(id: str, song: dict) -> tuple[str, list[str]]:
    return _haystack_of(song.get('title') or id, tuple(song.get('artists') or ()), song.get('album') or '')


@functools.lru_cache(maxsize=None)  # the picker searches the same songs on every keystroke
def _haystack_of(title: str, artists: tuple, album: str) -> tuple[str, list[tuple[str, frozenset]]]:
    hay = normalize(' '.join([title, *artists, album]))
    return hay, [(w, frozenset(w)) for w in re_word.findall(hay)]


def _distance(a: str, b: str, limit: int) -> int:
    """Edit distance counting swapped letters as one edit; gives up past `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def _gaps(token: str, hay: str) -> int | None:
    """Number of gaps if the letters of `token` appear in order in `hay`."""
    pos, gaps = -1, 0
    for c in token:
        j = hay.find(c, pos + 1)
        if j < 0:
            return None
        gaps += pos >= 0 and j != pos + 1
        pos = j
    return gaps


def _typo(token: str, words: list[tuple[str, frozenset]], limit: int) -> bool:
    letters = frozenset(token)
    # one edit adds or removes at most one distinct letter each way: a cheap test before the real one
    return any(len(letters ^ ws) <= 2 * limit and _distance(token, w, limit) <= limit for w, ws in words)


def _score(tokens: list[str], hay: str, words: list[tuple[str, frozenset]], tier: int) -> int | None:
    """Tier 0 allows only substrings, tier 1 also typos, tier 2 also scattered letters."""
    total = 0
    for t in tokens:
        i = hay.find(t)
        limit = 1 if len(t) < 8 else 2
        if i >= 0:
            total += len(t) * (3 if i == 0 or not hay[i - 1].isalnum() else 2)
        elif tier >= 1 and len(t) >= 4 and _typo(t, words, limit):
            total += len(t)
        elif tier >= 2 and (gaps := _gaps(t, hay)) is not None:
            total += max(1, len(t) - 2 * gaps)
        else:
            return None
    return total


def search(songs: dict, query: str, regex: bool = False, case: bool = False,
           best: bool = True, enough: int | None = None) -> list[str]:
    """
    Ids of the songs matching `query`, best first. Every word of the query must
    match the title, artists or album, ignoring case and accents. Substrings
    beat typos, and typos beat scattered letters. With `best`, only the best
    kind of match found is returned (what `ls` shows); otherwise all of them,
    stopping once there are `enough` (what the picker shows).
    """
    if regex:
        rx = re.compile(query, 0 if case else re.IGNORECASE)
        return [id for id, s in songs.items()
                if any(rx.search(f) for f in (s.get('title') or id, *(s.get('artists') or []), s.get('album') or '', id))]
    tokens = normalize(query).split()
    if not tokens:
        return list(songs)
    hays = {id: _haystack(id, s) for id, s in songs.items()}
    found = []
    for tier in range(3):
        scored = []
        for id, (hay, words) in hays.items():
            score = _score(tokens, hay, words, tier)
            if score is not None:
                scored.append((-score, len(scored), id))
        for _, _, id in sorted(scored):
            hays.pop(id)
            found.append(id)
        if found and (best or (enough and len(found) >= enough)):
            break
    return found
