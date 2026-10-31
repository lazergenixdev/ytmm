import argparse, json, logging, re, shutil, sys, time
from dataclasses import fields
from pathlib import Path
from rich.console import Console
from rich.markup import escape
from rich.prompt import Confirm
from . import __version__, config, library, status
from .config import Config
from .library import Library, LibraryError, describe, normalize, normalize_tag, parse_id
from .plan import AUDIO_EXTS, file_id, file_name, playlist_name

console = Console(highlight=False)

STIGNORE = {
    'library': '.*.tmp\n(?d).DS_Store\n',
    'music':   '.*.part\n.*.tmp\n(?d).DS_Store\n(?d)Thumbs.db\n',
}
STALE = 15 * 60  # warn when the server hasn't written status.json for this long


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


class output:
    def section(*values, **kwargs):
        console.print('[cyan]::', *values, **kwargs)
    def status(*values, **kwargs):
        console.print('', *values, **kwargs)
    def error(*values, **kwargs):
        console.print('[red]error[/]:', *values, **kwargs)
    def warn(*values, **kwargs):
        console.print('[yellow]warning[/]:', *values, **kwargs)
    def path(p):
        return f'[green1]"{escape(str(p))}"[/green1]'
    def song(id, s, long=False):
        artists = escape(', '.join(s.get('artists') or []))
        text = f'[medium_spring_green]{escape(s.get("title") or id)}[/]'
        if artists:
            text = f'[italic orchid1]{artists}[/] - {text}'
        extra = ', '.join(str(x) for x in (s.get('album'), s.get('year')) if x)
        return f'{text} [dim]({escape(extra)})[/]' if long and extra else text
    def ask(q, default=True):
        if not interactive():
            return default
        return Confirm.ask(f'[cyan]::[/] {q}', default=default, console=console)


def ago(t: float) -> str:
    s = int(time.time() - t)
    if s < 60:    return f'{max(s, 0)}s ago'
    if s < 3600:  return f'{s // 60} min ago'
    if s < 86400: return f'{s // 3600} h ago'
    return f'{s // 86400} days ago'


def until(t: float) -> str:
    s = int(t - time.time())
    if s <= 0:   return 'soon'
    if s < 3600: return f'in {max(1, s // 60)} min'
    return f'in {s // 3600} h'


def split_artists(text: str) -> list[str]:
    sep = ';' if ';' in text else ','
    return [a.strip() for a in text.split(sep) if a.strip()]


def join_artists(artists: list[str]) -> str:
    return ('; ' if any(',' in a for a in artists) else ', ').join(artists)


def parse_year(text: str) -> int | str:
    """'' removes the year."""
    text = text.strip()
    if not text:
        return ''
    if text.isdigit() and len(text) == 4:
        return int(text)
    raise ValueError(f'not a year: "{text}"')


def local_files(cfg: Config) -> dict[str, str]:
    """{video id: file name} in music/songs on this device."""
    from .worker import scan
    return {id: name for name in scan(cfg.songs_dir) if (id := file_id(name))}


#
# Picking songs
#

def song_label(id: str, s: dict) -> list:
    artists = ', '.join(s.get('artists') or [])
    label = [('class:artist', artists), ('', ' - ')] if artists else []
    label.append(('class:title', s.get('title') or id))
    extra = ' · '.join(str(x) for x in (s.get('album'), s.get('year')) if x)
    if extra:
        label.append(('class:dim', f'  {extra}'))
    if s.get('tags'):
        label.append(('class:tag', '  ' + ' '.join(f'#{t}' for t in s['tags'])))
    return label


def track_label(t, have: bool = False) -> list:
    label = [('class:title', t.title)]
    if t.artists:
        label += [('', ' - '), ('class:artist', ', '.join(t.artists))]
    extra = ' · '.join(str(x) for x in (t.album, t.year, t.duration, 'video' if t.video else None) if x)
    if extra:
        label.append(('class:dim', f'  {extra}'))
    if have:
        label.append(('class:ok', '  ✓ in library'))
    return label


def choose(songs: dict, query: list[str], regex=False, all=False, multi=True, message=None) -> list[str]:
    """The songs a command acts on: a single match is used directly, several open the picker."""
    q = ' '.join(query).strip()
    if q and (id := parse_id(q)) and id in songs:
        return [id]
    everything = sorted(songs, key=lambda i: normalize(describe(i, songs[i])))
    songs = {id: songs[id] for id in everything}  # equally good matches stay in alphabetical order
    try:
        ids = library.search(songs, q, regex=regex) if q else everything
    except re.error as e:
        output.error(f'bad regex ({escape(str(e))})')
        return []
    if not ids:
        output.error(f'no song matches "{escape(q)}"' if q else 'no songs to choose from')
        return []
    if all or (q and len(ids) == 1):
        return ids
    if not interactive():
        output.error(f'"{escape(q)}" matches {len(ids)} songs: be more specific, or pass --all' if q
                     else 'which songs? Give a search, or pass --all')
        for id in ids[:20]:
            output.status(output.song(id, songs[id]))
        if len(ids) > 20:
            output.status(f'[dim]... and {len(ids) - 20} more[/]')
        return []
    from .picker import pick
    if regex:
        rank = lambda text: library.search(songs, text, regex=True)
    else:
        rank = lambda text: library.search(songs, text, best=False, enough=500)
    return pick([(id, song_label(id, songs[id])) for id in everything], rank, q, multi, message) or []


#
# Commands
#

def cmd_init(args) -> int:
    path = config.config_path()
    if path.exists() and not args.force:
        output.error(f'{output.path(path)} already exists (pass --force to replace it)')
        return 1
    cfg = Config(root=Path(args.root).expanduser().resolve(), role=args.role)
    if args.name:
        cfg.device = args.name
    if args.inbox:
        cfg.inbox = args.inbox
    if args.notify:
        cfg.notify = args.notify
    for sub, ignore in STIGNORE.items():
        d = cfg.root / sub
        d.mkdir(parents=True, exist_ok=True)
        if not (d / '.stignore').exists():
            (d / '.stignore').write_text(ignore, encoding='utf-8')
    cfg.songs_dir.mkdir(parents=True, exist_ok=True)
    config.save(cfg, path)
    output.section(f'wrote {output.path(path)}')
    output.status(f'device [b]{escape(cfg.device)}[/] ({cfg.role}), library in {output.path(cfg.root)}')
    output.section('share these folders with Syncthing:')
    output.status(f'  ytmm-library  {escape(str(cfg.library_dir))}  (Send & Receive)')
    mode = 'Send Only' if cfg.role == 'server' else 'Receive Only'
    output.status(f'  ytmm-music    {escape(str(cfg.music_dir))}  ({mode})')
    if cfg.role == 'server':
        output.section('then check that downloading works with `ytmm canary`, and start `ytmm worker`')
    return 0


def cmd_add(cfg, lib, args) -> int:
    from . import sources
    items = args.items
    if not items:
        try:
            items = sources.find_urls(sources.clipboard())
        except sources.SourceError as e:
            output.error(escape(str(e)))
            return 1
        if not items:
            output.error('no YouTube links in the clipboard (copy one, or give links or a search)')
            return 1
        output.section(f'{len(items)} link{"s" if len(items) != 1 else ""} from the clipboard')
    kinds = [sources.classify(x) for x in items]
    try:
        tracks = None
        if all(kind for kind, _ in kinds):
            lone_word = len(items) == 1 and library.re_video_id.match(items[0].strip())
            tracks = lookup_links(kinds, args, lone_word)
        if tracks is None:
            tracks = search_results(' '.join(items), lib, args)
        if not tracks:
            return 1
        return add_tracks(lib, tracks, args)
    except sources.SourceError as e:
        output.error(escape(str(e)))
        return 1


def lookup_links(kinds, args, lone_word) -> list | None:
    """Tracks for video, playlist and album links. None: a lone word only looked like a video id."""
    from . import sources
    tracks = []
    for kind, value in kinds:
        if kind == 'video':
            if args.no_lookup:
                tracks.append(sources.Track(value, ''))
                continue
            with console.status(f'looking up {value}...'):
                t = sources.lookup(value)
            if t:
                tracks.append(t)
            elif lone_word:
                return None
            else:
                output.error(f'video {value} is unavailable')
        else:
            with console.status('reading the playlist...'):
                title, found = sources.playlist(value) if kind == 'playlist' else sources.album(value)
            output.section(f'{escape(title)}: {len(found)} songs')
            tracks += found
    return tracks


def search_results(query: str, lib, args) -> list | None:
    from . import sources
    with console.status(f'searching YouTube Music for "{escape(query)}"...'):
        results = sources.search(query, videos=args.videos)
    if not results:
        output.error(f'nothing found for "{escape(query)}"')
        return None
    if args.first:
        return results[:1]
    if not interactive():
        output.error('several results: pass --first to take the best one')
        return None
    from .picker import pick
    live, by_id = lib.live(), {t.id: t for t in results}
    songs = {t.id: t.fields() for t in results}
    picked = pick([(t.id, track_label(t, t.id in live)) for t in results],
                  lambda text: library.search(songs, text, best=False),
                  message=f'results for "{query}" (tab selects several)')
    return [by_id[id] for id in picked] if picked else None


def add_tracks(lib, tracks, args) -> int:
    from . import sources
    try:
        year = parse_year(args.year) if args.year else None
    except ValueError as e:
        output.error(escape(str(e)))
        return 1
    overrides = {'title': args.title, 'artists': split_artists(args.artist) if args.artist else None,
                 'album': args.album, 'year': year}
    overrides = {k: v for k, v in overrides.items() if v is not None}
    tracks = list({t.id: t for t in tracks}.values())
    if overrides and len(tracks) != 1:
        output.error('--title, --artist, --album and --year need exactly one song')
        return 1
    tags = [t for t in map(normalize_tag, args.tag or []) if t]
    live = lib.live()
    new = [t for t in tracks if t.id not in live]
    have = [t for t in tracks if t.id in live]
    if len(have) == 1 and len(tracks) == 1:
        output.status(f'already in the library: {output.song(have[0].id, live[have[0].id])}')
    elif have:
        output.status(f'[dim]{len(have)} of them already in the library[/]')
    if len(new) > 1 and not args.yes:
        for t in new:
            output.status(output.song(t.id, t.fields()))
        if not output.ask(f'Add {len(new)} songs?'):
            return 1
    if new and not args.no_lookup:
        with console.status('looking up release years...'):
            sources.complete(new)
    changed = False
    for t in new:
        lib.put(t.id, added=time.time(), **{**t.fields(), **overrides, **({'tags': tags} if tags else {})})
        output.status(f'[green]added[/] {output.song(t.id, lib.songs[t.id], long=True)}')
        changed = True
    for t in have:
        if tags and lib.put(t.id, tags=[*(live[t.id].get('tags') or []), *tags]):
            output.status(f'[blue]tagged[/] {output.song(t.id, live[t.id])}')
            changed = True
    if changed:
        lib.save()
    if new:
        output.section(f'the server will download {"it" if len(new) == 1 else "them"} (`ytmm status` shows progress)')
    return 0


SORTS = {
    'added':  lambda id, s: (s.get('added', 0), id),
    'artist': lambda id, s: (normalize(', '.join(s.get('artists') or [])), normalize(s.get('title') or id)),
    'title':  lambda id, s: normalize(s.get('title') or id),
    'album':  lambda id, s: (normalize(s.get('album') or ''), normalize(s.get('title') or id)),
    'year':   lambda id, s: (s.get('year') or 0, normalize(s.get('title') or id)),
}


def year_color(year: int) -> str:
    current = time.localtime().tm_year
    def mapf(lo, hi, v):
        return min(max((v - lo) / (hi - lo), 0), 1)
    if year >= current - 5:
        x = int(mapf(current - 5, current, year) * 255)
        return f'[rgb(255,{255 - x},0)]'
    if year >= current - 30:
        x = int(mapf(current - 30, current - 5, year) * 255)
        return f'[rgb({x},255,{255 - x})]'
    return '[rgb(0,255,255)]'


MARKS = {'here': '[green]✓[/]', 'failed': '[red]✗[/]', 'waiting': '[yellow]…[/]', 'server': '[blue]•[/]', 'unknown': '[dim]?[/]'}


def file_state(st: dict | None, local: dict):
    """Where a song's file is: here, on the server only, waiting for the server, failed, or unknown."""
    failed = (st or {}).get('failed') or {}
    waiting = set((st or {}).get('pending') or []) | {(st or {}).get('downloading')}
    def state(id):
        if id in local:   return 'here'
        if id in failed:  return 'failed'
        if id in waiting: return 'waiting'
        return 'server' if st else 'unknown'
    return state


def cmd_ls(cfg, lib, args) -> int:
    from rich.table import Column, Table
    songs = lib.deleted() if args.deleted else lib.live()
    if args.tag:
        tag = normalize_tag(args.tag)
        songs = {id: s for id, s in songs.items() if tag in (s.get('tags') or [])}
    query = ' '.join(args.query)
    try:
        ids = library.search(songs, query, regex=args.regex, case=args.case_sensitive)
    except re.error as e:
        output.error(f'bad regex ({escape(str(e))})')
        return 1
    st = status.read(cfg.status_file)
    local = local_files(cfg)
    if args.downloaded is not None:
        ids = [id for id in ids if (id in local) == args.downloaded]
    if args.failed:
        ids = [id for id in ids if id in ((st or {}).get('failed') or {})]
    ids.sort(key=lambda id: SORTS[args.sort](id, songs[id]), reverse=args.reverse)
    if args.first:
        ids = ids[:args.first]
    if args.last:
        ids = ids[-args.last:]

    if args.files:
        for id in ids:
            if id in local:
                print(cfg.songs_dir / local[id])
        return 0
    if args.ids:
        for id in ids:
            print(id)
        return 0
    state = file_state(st, local)
    if args.count:
        states = [state(id) for id in ids]
        table = Table(
            Column(header='Total ',   style='dodger_blue1', no_wrap=True),
            Column(header='Here ',    style='green1',       no_wrap=True),
            Column(header='Waiting ', style='yellow',       no_wrap=True),
            Column(header='Failed ',  style='orange_red1',  no_wrap=True),
            box=None,
        )
        table.add_row(str(len(ids)), *(str(states.count(s)) for s in ('here', 'waiting', 'failed')))
        console.print(table)
        return 0
    if not ids:
        if query or args.tag:
            output.status('no songs match')
        else:
            output.status('no songs yet: `ytmm add <link or search>` adds some')
        return 0

    wide = console.width >= 100
    has_tags = any(songs[id].get('tags') for id in ids)
    first = Column(header='Removed', style='grey62', no_wrap=True) if args.deleted else Column(header='', no_wrap=True, width=1)
    columns = [first, Column(header='Year', justify='center', no_wrap=True, min_width=4),
               Column(header='Artists', style='italic orchid1', no_wrap=True, ratio=2),
               Column(header='Title', style='medium_spring_green', no_wrap=True, ratio=3)]
    if wide:
        columns.append(Column(header='Album', style='grey62', no_wrap=True, ratio=2))
    if has_tags:
        columns.append(Column(header='Tags', style='slate_blue1', no_wrap=True, ratio=1))
    if wide:
        columns.append(Column(header='ID', style='grey39', no_wrap=True, min_width=11))
    table = Table(*columns, box=None, expand=True)
    for id in ids:
        s = songs[id]
        year = f'{year_color(s["year"])}{s["year"]}' if s.get('year') else '[dim]--[/]'
        first = f'{ago(s.get("updated", 0))} ({escape(s.get("updated_by", "?"))})' if args.deleted else MARKS[state(id)]
        row = [first, year, escape(', '.join(s.get('artists') or [])), escape(s.get('title') or id)]
        if wide:
            row.append(escape(s.get('album') or ''))
        if has_tags:
            row.append(escape(' '.join(f'#{t}' for t in s.get('tags') or [])))
        if wide:
            row.append(id)
        table.add_row(*row)
    console.print(table)
    if args.deleted:
        output.status('[dim]`ytmm restore <song>` brings a song back[/]')
    else:
        output.status('[dim]✓ on this device · • on the server, not synced here yet · … waiting for the server · ✗ failed[/]')
    return 0


def prompt_song(id: str, song: dict) -> dict | None:
    from prompt_toolkit import prompt
    output.section(f'editing {output.song(id, song)} [dim]{id} (enter keeps a value, ctrl+c stops)[/]')
    try:
        title   = prompt('  title:   ', default=song.get('title') or '')
        artists = prompt('  artists: ', default=join_artists(song.get('artists') or []))
        album   = prompt('  album:   ', default=song.get('album') or '')
        while True:
            try:
                year = parse_year(prompt('  year:    ', default=str(song.get('year') or '')))
                break
            except ValueError as e:
                output.error(escape(str(e)))
    except (KeyboardInterrupt, EOFError):
        return None
    return {'title': title.strip() or None, 'artists': split_artists(artists), 'album': album.strip(), 'year': year}


def cmd_edit(cfg, lib, args) -> int:
    from . import sources
    try:
        year = parse_year(args.year) if args.year is not None else None
    except ValueError as e:
        output.error(escape(str(e)))
        return 1
    changes = {'title': args.title, 'artists': split_artists(args.artist) if args.artist is not None else None,
               'album': args.album, 'year': year}
    changes = {k: v for k, v in changes.items() if v is not None}
    if not (changes or args.refresh or interactive()):
        output.error('give the new values (--title, --artist, --album, --year) or --refresh')
        return 1
    songs = lib.live()
    ids = choose(songs, args.query, regex=args.regex, all=args.all, message='songs to edit (tab selects several)')
    if not ids:
        return 1
    if 'title' in changes and len(ids) > 1:
        output.error('--title would give all of them the same title')
        return 1
    edited = 0
    try:
        for id in ids:
            if args.refresh:
                with console.status(f'looking up {id}...'):
                    t = sources.lookup(id)
                    fields = sources.complete([t])[0].fields() if t else {}
                if not t:
                    output.error(f'{id} is not on YouTube Music anymore')
            else:
                fields = changes or prompt_song(id, songs[id])
            if fields is None:
                break
            if lib.put(id, **fields):
                edited += 1
                output.status(f'[green]edited[/] {output.song(id, lib.songs[id], long=True)}')
            else:
                output.status(f'[dim]unchanged[/] {output.song(id, songs[id])}')
    except sources.SourceError as e:
        output.error(escape(str(e)))
    if edited:
        lib.save()
        output.section('the server will rename and retag the files')
    return 0


def cmd_rm(cfg, lib, args) -> int:
    songs = lib.live()
    ids = choose(songs, args.query, regex=args.regex, all=args.all, message='songs to remove (tab selects several)')
    if not ids:
        return 1
    if not args.yes:
        output.section(f'remove {len(ids)} song{"s" if len(ids) != 1 else ""}:')
        for id in ids:
            output.status(output.song(id, songs[id]))
        if not output.ask('Proceed?'):
            return 1
    for id in ids:
        lib.delete(id)
        output.status(f'[red]removed[/] {output.song(id, songs[id])}')
    lib.save()
    output.section('the server moves the files to its trash; `ytmm restore` brings them back')
    return 0


def cmd_restore(cfg, lib, args) -> int:
    songs = lib.deleted()
    if not songs:
        output.status('no removed songs')
        return 0
    ids = choose(songs, args.query, regex=args.regex, all=args.all, message='songs to restore (tab selects several)')
    for id in ids:
        lib.put(id)
        output.status(f'[green]restored[/] {output.song(id, songs[id])}')
    if ids:
        lib.save()
        output.section('the server takes the files back out of its trash (or downloads them again)')
    return 0 if ids else 1


def cmd_tag(cfg, lib, args) -> int:
    tag = normalize_tag(args.tag)
    if not tag:
        output.error('give a tag name')
        return 1
    songs = {id: s for id, s in lib.live().items() if tag not in (s.get('tags') or [])}
    ids = choose(songs, args.query, regex=args.regex, all=args.all, message=f'songs to tag #{tag} (tab selects several)')
    for id in ids:
        lib.put(id, tags=[*(songs[id].get('tags') or []), tag])
        output.status(f'[blue]#{escape(tag)}[/] {output.song(id, songs[id])}')
    if ids:
        lib.save()
        output.section(f'the server writes the playlist playlists/{escape(playlist_name(tag))}')
    return 0 if ids else 1


def cmd_untag(cfg, lib, args) -> int:
    tag = normalize_tag(args.tag)
    songs = {id: s for id, s in lib.live().items() if tag in (s.get('tags') or [])}
    if not songs:
        output.error(f'no song is tagged #{escape(tag)}')
        return 1
    ids = choose(songs, args.query, regex=args.regex, all=args.all, message=f'songs to untag #{tag} (tab selects several)')
    for id in ids:
        lib.put(id, tags=[t for t in songs[id].get('tags') or [] if t != tag])
        output.status(f'[dim]untagged[/] {output.song(id, songs[id])}')
    if ids:
        lib.save()
    return 0 if ids else 1


def cmd_tags(cfg, lib, args) -> int:
    tags = lib.tags()
    if not tags:
        output.status('no tags yet: `ytmm tag <tag> <songs>` adds one, and each tag becomes a playlist')
    for tag, n in tags.items():
        output.status(f'[slate_blue1]#{escape(tag)}[/] {n} song{"s" if n != 1 else ""} [dim]playlists/{escape(playlist_name(tag))}[/]')
    return 0


def cmd_status(cfg, lib, args) -> int:
    st = status.read(cfg.status_file)
    if not st:
        output.section('no status.json yet: is the worker running, and is the library folder shared with the server?')
    else:
        stale = time.time() - st.get('seen', 0) >= STALE
        canary = st.get('canary') or {}
        parts = [f'server [b]{escape(st.get("server", "?"))}[/] last seen {ago(st.get("seen", 0))}']
        if st.get('ytdlp'):
            parts.append(f'yt-dlp {escape(st["ytdlp"])}')
        if canary:
            parts.append('canary [green]OK[/]' if canary.get('ok') else 'canary [red]FAILED[/]')
        line = ' · '.join(parts)
        output.section(f'[red]{line}[/]' if stale else line)
        if stale:
            output.status('[red]the server stopped reporting: is the worker running, and is Syncthing connected?[/]')
        if canary and not canary.get('ok'):
            output.status(f'  [red]{escape(canary.get("error") or "")}[/] [dim]({ago(canary.get("time", 0))})[/]')
        if st.get('error'):
            output.status(f'  [red]not syncing: {escape(st["error"])}[/]')
        failed, blocked = st.get('failed') or {}, st.get('blocked')
        waiting = len(st.get('pending') or []) + bool(st.get('downloading'))
        output.section(f'{waiting} downloading, {len(failed)} failed, {blocked["count"] if blocked else 0} pending trash')
        for id, f in failed.items():
            song = lib.songs.get(id) or {}
            output.status(f'  [red]✗[/] {escape(f.get("error") or "?")}: {id} '
                          f'[dim]({escape(describe(id, song))}; retry {until(f.get("retry") or 0)})[/]')
        if blocked:
            output.status(f'  [yellow]{escape(blocked.get("reason") or "blocked")}: '
                          f'{blocked["count"]} files (limit {blocked.get("limit")}), e.g.[/]')
            for name in (blocked.get('files') or [])[:5]:
                output.status(f'    [dim]{escape(name)}[/]')
            if blocked.get('token'):
                output.status('  run `ytmm approve` if those files really should go')
        inbox = st.get('inbox')
        if inbox:
            text = f'inbox checked {ago(inbox.get("time", 0))}'
            if inbox.get('last_import'):
                text += f', last import {ago(inbox["last_import"])}'
            output.section(text if inbox.get('ok') else f'{text}: [red]{escape(inbox.get("error") or "failed")}[/]')
        update = st.get('update')
        if update and not update.get('ok'):
            output.status(f'  [red]the daily update failed {ago(update.get("time", 0))}[/] (see the worker log)')
    live, local = lib.live(), local_files(cfg)
    here = len(set(local) & set(live))
    output.section(f'this device ([b]{escape(cfg.device)}[/], {cfg.role}): {len(live)} songs, {here} files here')
    return 0


def cmd_approve(cfg, lib, args) -> int:
    blocked = (status.read(cfg.status_file) or {}).get('blocked')
    if not blocked or not blocked.get('token'):
        output.status('nothing to approve: the server has not refused to trash anything')
        return 0
    output.section(f'the server wants to trash {blocked["count"]} files (its limit is {blocked.get("limit")}), e.g.:')
    for name in (blocked.get('files') or [])[:10]:
        output.status(f'  {escape(name)}')
    if not args.yes and not output.ask('Allow it?', default=False):
        return 1
    status.write(cfg.approve_file, {'token': blocked['token'], 'by': cfg.device, 'time': time.time()})
    output.status('approved: the server trashes them on its next pass, and keeps them in its trash for 30 days')
    return 0


def cmd_migrate(cfg, args) -> int:
    from . import migrate
    src = Path(args.music_json).expanduser()
    try:
        db = json.loads(src.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        output.error(f'cannot read {output.path(src)} ({escape(str(e))})')
        return 1
    if not isinstance(db, dict) or not isinstance(db.get('data'), list):
        output.error(f'{output.path(src)} is not a v0.2 music.json')
        return 1
    try:
        lib = Library(cfg.library_dir, cfg.device).load()
    except (OSError, LibraryError) as e:
        output.error(escape(str(e)))
        return 1
    songs = migrate.convert(db, time.time())
    new = {id: s for id, s in songs.items() if id not in lib.songs}  # tombstones stay deleted
    known = {id: s for id, s in {**songs, **lib.songs}.items() if not s.get('deleted')}

    folder = Path(args.files).expanduser() if args.files else migrate.files_dir(src, db)
    moves, unmatched, here = [], [], local_files(cfg)
    if folder and folder.is_dir():
        try:
            import mutagen  # noqa: F401
            from .download import read_id
        except ImportError:
            read_id = None
        paths = [p for p in folder.rglob('*') if p.is_file() and p.suffix.lower() in AUDIO_EXTS
                 and not p.resolve().is_relative_to(cfg.songs_dir.resolve())]
        found, unmatched = migrate.match_files(known, paths, read_id)
        for p, id in found.items():
            if id in here:
                unmatched.append(p)
            else:
                moves.append((p, cfg.songs_dir / file_name(id, known[id], p.suffix.lower())))

    output.section(f'{len(songs)} songs in {output.path(src)}, {len(new)} of them new to the library')
    tags = sorted({t for s in new.values() for t in s.get('tags', [])})
    if tags:
        output.status(f'playlists become tags: {", ".join("#" + escape(t) for t in tags)}')
    if folder:
        output.status(f'{len(moves)} files to move from {output.path(folder)} to {output.path(cfg.songs_dir)}')
    else:
        output.warn('found no folder with the old files (pass --files DIR); the server would download every song')
    if unmatched:
        output.status(f'{len(unmatched)} files stay where they are (no song matches them, or it already has a file):')
        for p in unmatched[:10]:
            output.status(f'  [dim]{escape(str(p))}[/]')
    with_file = set(here) | {file_id(dst.name) for _, dst in moves}
    missing = [id for id in known if id not in with_file]
    if missing:
        output.status(f'the server will download {len(missing)} songs that have no file')
    if args.dry_run:
        for p, dst in moves:
            output.status(f'  [dim]{escape(p.name)}[/] → {escape(dst.name)}')
        output.section('dry run: nothing was changed')
        return 0
    if cfg.role != 'server':
        output.warn('migrate is meant to run on the server, the only device that writes music/')
    if not args.yes and not output.ask('Proceed?'):
        return 1
    for id, s in new.items():
        lib.put(id, **s)
    lib.save()
    cfg.songs_dir.mkdir(parents=True, exist_ok=True)
    for p, dst in moves:
        shutil.move(p, dst)
    output.status(f'[green]added[/] {len(new)} songs, moved {len(moves)} files')
    output.section('the worker tags every file (with cover art) and downloads the missing songs')
    return 0


def log_line(line: str) -> None:
    console.print(f'[dim]{time.strftime("%Y-%m-%d %H:%M:%S")}[/]', escape(line), soft_wrap=True)


def make_downloader(cfg):
    from .download import DownloadError, make
    d = make(cfg)
    try:
        d.check()
    except DownloadError as e:
        output.error(escape(str(e)))
        return None
    return d


def cmd_worker(cfg, args) -> int:
    from .worker import Worker
    if cfg.role != 'server':
        output.error('the worker runs on the server: set role = "server" in its config')
        return 1
    d = make_downloader(cfg)
    if d is None:
        return 1
    w = Worker(cfg, downloader=d, on_log=log_line)
    if args.retag:
        w.state.data['tagged'] = {}
        w.state.save()
        output.status('every file will be tagged again')
    if args.once:
        w.ytdlp = d.version()
        w.run_once()
        return 0
    output.section(f'worker for {output.path(cfg.root)}: checking for changes every {cfg.poll_interval:g}s (ctrl+c stops)')
    if cfg.inbox:
        output.status(f'inbox playlist checked every {cfg.inbox_interval / 60:g} min')
    try:
        w.run()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_canary(cfg, args) -> int:
    from .download import tool_versions
    from .worker import Worker
    for tool, version in tool_versions(cfg).items():
        mark = '[green]✓[/]' if version else '[red]✗[/]'
        output.status(f'{mark} {tool} {escape(version or "not found")}')
    output.section(f'downloading {cfg.canary} ...')
    w = Worker(cfg, on_log=lambda line: output.status(escape(line)))
    return 0 if w.run_canary(record=False) else 1


def cmd_sync(cfg, args) -> int:
    from .worker import Worker
    if not args.local:
        output.error('computers no longer download songs: the server does (`ytmm status` shows how it is doing)')
        output.status('when the server is down for a long time, `ytmm sync --local` does its work on this computer')
        return 1
    d = make_downloader(cfg)
    if d is None:
        return 1
    if cfg.role != 'server':
        output.warn('in Syncthing on this computer, switch the "ytmm-music" folder to Send & Receive first, '
                    'and back to Receive Only afterwards')
    if not args.yes and not output.ask('Download, tag and trash files on this computer?'):
        return 1
    w = Worker(cfg, downloader=d, write_status=False, on_log=lambda line: output.status(escape(line)))
    w.run_once()
    if not w.lines:
        output.status('nothing to do')
    return 0


def cmd_config(cfg, args) -> int:
    output.section(f'config {output.path(config.config_path())}')
    for f in fields(Config):
        output.status(f'{f.name} = {escape(config.toml_value(getattr(cfg, f.name)))}')
    return 0


def create_parser():
    parser = argparse.ArgumentParser(prog='ytmm', description=f'YouTube Music Manager (v{__version__})')
    parser.add_argument('--version', action='version', version=f'ytmm {__version__}')
    sub = parser.add_subparsers(metavar='SUBCOMMAND', dest='command')

    def song_args(p, what):
        p.add_argument('query', nargs='*', metavar='SONG', help=f'search for the songs to {what} (a video id or URL works too)')
        p.add_argument('--regex', action='store_true', help='SONG is a regular expression')
        p.add_argument('--all', action='store_true', help='take every match instead of picking')

    def meta_args(p):
        p.add_argument('-t', '--title')
        p.add_argument('-a', '--artist', metavar='ARTISTS', help='comma-separated (";" if a name has a comma)')
        p.add_argument('--album', help='"" removes it')
        p.add_argument('--year', help='"" removes it')

    p = sub.add_parser('init', help="write this device's config and create the folders")
    p.add_argument('root', help='folder for library/ and music/ (e.g. ~/Music/ytmm)')
    p.add_argument('--role', choices=config.ROLES, default='client')
    p.add_argument('--name', help='device name (default: hostname)')
    p.add_argument('--inbox', help='server: YouTube Music playlist to import songs from')
    p.add_argument('--notify', help='server: ntfy topic URL for push notifications')
    p.add_argument('--force', action='store_true', help='replace an existing config')

    p = sub.add_parser('add', help='add songs from links (videos, playlists, albums), a search, or the clipboard')
    p.add_argument('items', nargs='*', metavar='LINK|SEARCH', help='nothing: take the links in the clipboard')
    meta_args(p)
    p.add_argument('--tag', action='append', help='tag the songs (repeatable)')
    p.add_argument('--videos', action='store_true', help='search videos instead of songs')
    p.add_argument('--first', action='store_true', help='take the best search result without asking')
    p.add_argument('--no-lookup', action='store_true', help="don't look up metadata (the server fills it in)")
    p.add_argument('-y', '--yes', action='store_true', help="don't ask before adding several songs")

    p = sub.add_parser('ls', help='list songs (fuzzy search on title, artists and album)')
    p.add_argument('query', nargs='*', metavar='SEARCH')
    p.add_argument('--regex', action='store_true', help='SEARCH is a regular expression')
    p.add_argument('-c', '--case-sensitive', action='store_true', help='for --regex')
    p.add_argument('-t', '--tag', help='only songs with this tag')
    p.add_argument('-D', '--downloaded', action=argparse.BooleanOptionalAction, help='only songs [not] on this device')
    p.add_argument('--failed', action='store_true', help='only songs the server failed to download')
    p.add_argument('-d', '--deleted', action='store_true', help='list removed songs instead')
    p.add_argument('-s', '--sort', choices=list(SORTS), default='added')
    p.add_argument('-r', '--reverse', action='store_true')
    p.add_argument('-f', '--first', type=int, metavar='N', help='only the first N songs')
    p.add_argument('-l', '--last', type=int, metavar='N', help='only the last N songs')
    p.add_argument('-F', '--files', action='store_true', help='print the paths of the files on this device')
    p.add_argument('--ids', action='store_true', help='print video ids')
    p.add_argument('-n', '--count', action='store_true', help='only count them')

    p = sub.add_parser('edit', help="fix songs' title, artists, album or year")
    song_args(p, 'edit')
    meta_args(p)
    p.add_argument('--refresh', action='store_true', help='look the metadata up on YouTube Music again')

    p = sub.add_parser('rm', help='remove songs (the server keeps their files in its trash for 30 days)')
    song_args(p, 'remove')
    p.add_argument('-y', '--yes', action='store_true', help="don't ask for confirmation")

    p = sub.add_parser('restore', help='bring back removed songs')
    song_args(p, 'restore')

    p = sub.add_parser('tag', help='tag songs; each tag becomes a playlist')
    p.add_argument('tag')
    song_args(p, 'tag')

    p = sub.add_parser('untag', help='remove a tag from songs')
    p.add_argument('tag')
    song_args(p, 'untag')

    sub.add_parser('tags', help='list tags')
    sub.add_parser('status', help='what the server is doing')

    p = sub.add_parser('approve', help='let the server trash more files than its safety limit')
    p.add_argument('-y', '--yes', action='store_true', help="don't ask for confirmation")

    p = sub.add_parser('migrate', help='import a v0.2 music.json and its files (run on the server)')
    p.add_argument('music_json')
    p.add_argument('--files', metavar='DIR', help='folder with the old files (default: from music.json)')
    p.add_argument('--dry-run', action='store_true', help='only show what would happen')
    p.add_argument('-y', '--yes', action='store_true', help="don't ask for confirmation")

    p = sub.add_parser('worker', help='server: download and tag songs whenever library.json changes')
    p.add_argument('--once', action='store_true', help='run a single pass and exit')
    p.add_argument('--retag', action='store_true', help='rewrite the tags of every file')

    sub.add_parser('canary', help='check that downloading works on this machine')

    p = sub.add_parser('sync', help="fallback: do the server's work on this computer")
    p.add_argument('--local', action='store_true', help='download on this computer (when the server is down for long)')
    p.add_argument('-y', '--yes', action='store_true', help="don't ask for confirmation")

    sub.add_parser('config', help="show this device's config")
    return parser


COMMANDS = {
    'add': cmd_add, 'ls': cmd_ls, 'edit': cmd_edit, 'rm': cmd_rm, 'restore': cmd_restore,
    'tag': cmd_tag, 'untag': cmd_untag, 'tags': cmd_tags, 'status': cmd_status, 'approve': cmd_approve,
}
TOOLS = {'migrate': cmd_migrate, 'worker': cmd_worker, 'canary': cmd_canary, 'sync': cmd_sync, 'config': cmd_config}


def main(argv=None) -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    logging.basicConfig(stream=sys.stderr, format='%(levelname)s: %(message)s')
    parser = create_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_usage()
        return 0
    try:
        if args.command == 'init':
            return cmd_init(args)
        try:
            cfg = config.load()
        except (FileNotFoundError, ValueError) as e:
            output.error(escape(str(e)))
            return 1
        if args.command in TOOLS:
            return TOOLS[args.command](cfg, args)
        try:
            lib = Library(cfg.library_dir, cfg.device).load()
        except (OSError, LibraryError) as e:
            output.error(escape(str(e)))
            return 1
        if lib.merged_conflicts:
            output.status(f'[dim]merged {len(lib.merged_conflicts)} Syncthing conflict copies of library.json[/]')
        return COMMANDS[args.command](cfg, lib, args)
    except KeyboardInterrupt:
        return 130
