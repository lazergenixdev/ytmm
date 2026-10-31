"""Client commands edit the shared library; only the worker touches audio."""
import argparse
import logging
import re
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

from . import config, status
from .config import Config
from .library import Library, parse_id
from .plan import file_id
from .selection import find, pick

console = Console(highlight=False)


def create_parser():
    parser = argparse.ArgumentParser(description='YouTube Music Manager v2')
    parser.add_argument('--version', action='version', version='ytmm 2.0.0')
    sub = parser.add_subparsers(dest='command')
    p = sub.add_parser('init', help='configure this device and create sync folders')
    p.add_argument('root', type=Path)
    p.add_argument('--role', choices=config.ROLES, default='client')
    p.add_argument('--name')
    p.add_argument('--inbox', help='server: public or unlisted YouTube Music playlist URL')
    p.add_argument('--force', action='store_true', help='replace existing device config')

    p = sub.add_parser('add', help='add URLs, a playlist/album, search terms, or clipboard URLs')
    p.add_argument('items', nargs='*')
    p.add_argument('-t', '--title', help='explicit metadata; a single video can be added offline')
    p.add_argument('-a', '--artist', '--artists', dest='artists', help='comma-separated artists')
    p.add_argument('--album')
    p.add_argument('--year', type=int)
    p.add_argument('--restore', action='store_true', help='allow explicitly re-adding deleted songs')

    p = sub.add_parser('ls', aliases=['query'], help='fuzzy search title, artists, album, or tags')
    p.add_argument('query', nargs='*')
    p.add_argument('--regex', action='store_true')
    p.add_argument('-d', '--deleted', action='store_true')
    p.add_argument('--files', action='store_true', help='print local paths instead of the table')
    p.add_argument('-n', '--count', action='store_true')

    for name in ('edit', 'rm', 'tag'):
        p = sub.add_parser(name, help={'edit': 'edit song metadata', 'rm': 'remove songs from the library',
                                     'tag': 'add or remove a playlist tag'}[name])
        if name == 'tag':
            p.add_argument('tag')
            p.add_argument('--remove', action='store_true')
        p.add_argument('query', nargs='+', help='video ID, URL, or fuzzy search terms')
        p.add_argument('--regex', action='store_true')
        p.add_argument('--all', action='store_true', help='apply to every match without the picker')
        if name == 'edit':
            p.add_argument('-t', '--title')
            p.add_argument('-a', '--artist', '--artists', dest='artists')
            p.add_argument('--album', help='use an empty string to clear')
            p.add_argument('--year', type=int, help='0 clears the year')

    sub.add_parser('status', help='read the server status')
    p = sub.add_parser('worker', help='server: download, tag, and maintain the shared music')
    p.add_argument('--once', action='store_true')
    p = sub.add_parser('sync', help='explicit fallback for a computer with server tools installed')
    p.add_argument('--local', action='store_true', required=True,
                   help='run one local worker pass; switch this music share to Send & Receive first')
    p = sub.add_parser('migrate', help='server: convert an old music.json and move its audio')
    p.add_argument('source', type=Path)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--music-dir', type=Path, help='override the legacy audio directory')
    return parser


def cmd_init(args):
    if config.config_path().exists() and not args.force:
        raise ValueError('device config already exists; use --force to replace it')
    cfg = Config(root=args.root.expanduser().resolve(), role=args.role)
    if args.name:
        cfg.device = args.name
    if args.inbox:
        if cfg.role != 'server':
            raise ValueError('--inbox is a server setting')
        cfg.inbox_playlist = args.inbox
    cfg.__post_init__()
    for directory, ignore in ((cfg.library_dir, '.*.tmp\n'),
                              (cfg.music_dir, '.*.part\n.*.tmp\n.ytmm-*\n')):
        directory.mkdir(parents=True, exist_ok=True)
        ignore_path = directory / '.stignore'
        if not ignore_path.exists():
            ignore_path.write_text(ignore, encoding='utf-8')
    cfg.songs_dir.mkdir(parents=True, exist_ok=True)
    lib = Library(cfg.library_dir, cfg.device).load()
    if not lib.path.exists():
        lib.save()
    path = config.save(cfg)
    console.print(f'Configured {cfg.device} ({cfg.role}) at {path}', markup=False)
    console.print(f'Library: {cfg.library_dir} — Syncthing Send & Receive', markup=False)
    mode = 'Send Only' if cfg.role == 'server' else 'Receive Only'
    console.print(f'Music:   {cfg.music_dir} — Syncthing {mode}', markup=False)


def _metadata(args):
    fields = {key: getattr(args, key) for key in ('title', 'album', 'year')
              if getattr(args, key, None) is not None}
    if getattr(args, 'artists', None) is not None:
        fields['artists'] = [s.strip() for s in args.artists.split(',') if s.strip()]
    if 'year' in fields:
        if not 0 <= fields['year'] <= 9999:
            raise ValueError('year must be between 1 and 9999, or 0 to clear it')
        if fields['year'] == 0:
            fields['year'] = ''
    return fields


def cmd_add(cfg, lib, args):
    from .sources import Source, SourceError, reference, read_clipboard

    def is_source(value):
        try:
            reference(value)
            return True
        except SourceError:
            return False

    items = args.items
    if not items:
        items = read_clipboard().split()
        if not items or any(not is_source(item) for item in items):
            raise ValueError('clipboard must contain YouTube URLs or video IDs')
    fields = _metadata(args)
    source = Source()
    if len(items) == 1 and is_source(items[0]) and reference(items[0])[0] == 'song' and args.title is not None:
        entries = [(parse_id(items[0]), fields)]
    elif all(is_source(item) for item in items):
        entries = []
        for item in items:
            entries.extend(source.resolve(item))
    else:
        if any(is_source(item) or '://' in item for item in items):
            raise ValueError('add accepts URLs/IDs together, or one search query')
        results = source.search(' '.join(items))
        if not results:
            raise ValueError('no YouTube Music search results')
        choices = dict(results)
        ids = pick(choices, list(choices), title='Select songs to add')
        entries = [(id, choices[id]) for id in ids]
    if fields and len(entries) > 1:
        raise ValueError('metadata overrides apply to one song at a time')
    added = skipped = 0
    for id, song in entries:
        if id in lib.songs and (not lib.songs[id].get('deleted') or not args.restore):
            skipped += 1
            continue
        lib.put(id, **(song | fields))
        added += 1
    if added:
        lib.save()
    console.print(f'Added {added} songs; skipped {skipped} existing or deleted entries. Audio will sync from the server.')


def _select(lib, args):
    query = ' '.join(args.query)
    ids = find(lib.live(), query, regex=args.regex)
    if not ids:
        raise ValueError(f'no song matches {query!r}')
    if len(ids) == 1 or args.all:
        return ids
    return pick(lib.songs, ids, title=f'Select songs to {args.command}')


def cmd_modify(cfg, lib, args):
    fields = _metadata(args) if args.command == 'edit' else {}
    if args.command == 'edit' and not fields:
        raise ValueError('provide --title, --artist, --album, or --year')
    if args.command == 'tag' and (not args.tag.strip() or len(args.tag) > 100):
        raise ValueError('tag must contain 1–100 characters')
    ids = _select(lib, args)
    for id in ids:
        if args.command == 'rm':
            lib.delete(id)
        elif args.command == 'edit':
            lib.put(id, **fields)
        else:
            tags = set(lib.songs[id].get('tags', []))
            if args.remove:
                tags.discard(args.tag)
            else:
                tags.add(args.tag)
            lib.put(id, tags=sorted(tags))
    if ids:
        lib.save()
    console.print(f'{args.command}: {len(ids)} songs updated.')


def cmd_ls(cfg, lib, args):
    songs = lib.songs if args.deleted else lib.live()
    ids = find(songs, ' '.join(args.query), regex=args.regex)
    if args.count:
        console.print(len(ids))
        return
    local = {file_id(p.name): p for p in cfg.songs_dir.glob('*.mp3') if p.is_file()}
    st = status.read(cfg.status_file) or {}
    table = Table('State', 'ID', 'Artists', 'Title', 'Album', 'Year', 'Tags', box=None)
    for id in ids:
        song = songs[id]
        if args.files:
            if id in local:
                console.print(str(local[id]), markup=False)
            continue
        state = ('deleted' if song.get('deleted') else 'local' if id in local else
                 'failed' if id in st.get('failed', {}) else 'pending')
        values = (state, id, ', '.join(song.get('artists', [])), song.get('title', ''),
                  song.get('album') or '', str(song.get('year') or ''), ', '.join(song.get('tags', [])))
        table.add_row(*(Text(value) for value in values))
    console.print(table)


def cmd_status(cfg):
    st = status.read(cfg.status_file)
    if not st:
        console.print('No server status yet. Start the server worker and sync the library folder.')
        return
    age = max(0, int(time.time() - st.get('seen', 0)))
    version = st.get('yt_dlp') or 'unknown'
    console.print(f'Server {st.get("server", "?")} last seen {age // 60} min ago · yt-dlp {version}', markup=False)
    console.print(f'{len(st.get("downloading", []))} downloading · {len(st.get("pending", []))} pending · '
                  f'{len(st.get("failed", {}))} failed · {st.get("pending_trash", 0)} pending trash')
    for id, err in st.get('failed', {}).items():
        console.print(f'{id}: {err}', markup=False)
    if st.get('blocked'):
        console.print(f'Blocked: {st["blocked"]}', markup=False)
    if st.get('error'):
        console.print(f'Error: {st["error"]}', markup=False)
    inbox = st.get('inbox') or {}
    if inbox.get('error'):
        console.print(f'Inbox: {inbox["error"]}', markup=False)
    canary = st.get('canary') or {}
    if canary:
        console.print('Canary: OK' if canary.get('ok') else f'Canary failed: {canary.get("error", "unknown")}', markup=False)
        if canary.get('notification_error'):
            console.print(f'Notification failed: {canary["notification_error"]}', markup=False)


def main(argv=None):
    logging.basicConfig(level=logging.WARNING)
    parser = create_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    try:
        if args.command == 'init':
            cmd_init(args)
            return 0
        cfg = config.load()
        if args.command in ('worker', 'sync'):
            from .worker import Worker
            worker = Worker(cfg, local=args.command == 'sync')
            if args.command == 'sync' or args.once:
                for line in worker.run_once():
                    console.print(line, markup=False)
                st = status.read(cfg.status_file) or {}
                return 1 if st.get('blocked') or st.get('error') or st.get('failed') else 0
            worker.run(on_log=lambda line: console.print(line, markup=False))
            return 0
        if args.command == 'migrate':
            from .migrate import migrate
            report = migrate(cfg, args.source, dry_run=args.dry_run, music_dir=args.music_dir)
            prefix = 'Dry run: ' if report['dry_run'] else ''
            console.print(f'{prefix}{len(report["added"])} added, {len(report["moved"])} files moved, '
                          f'{len(report["missing"])} missing, {len(report["skipped"])} skipped.')
            for message in report['skipped']:
                console.print(message, markup=False)
            return 0
        if args.command == 'status':
            cmd_status(cfg)
            return 0
        lib = Library(cfg.library_dir, cfg.device).load()
        if args.command == 'add':
            cmd_add(cfg, lib, args)
        elif args.command in ('ls', 'query'):
            cmd_ls(cfg, lib, args)
        else:
            cmd_modify(cfg, lib, args)
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError, re.error) as exc:
        console.print(f'Error: {exc}', markup=False, style='red')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
