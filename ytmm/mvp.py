"""
Sync MVP: client commands + server worker for testing library sync between
devices. Downloads are faked (see download.py), so no yt-dlp is needed.

    python -m ytmm.mvp init ~/Music/ytmm --role server --name homeserver
    python -m ytmm.mvp add dQw4w9WgXcQ -t "Never Gonna Give You Up" -a "Rick Astley"
    python -m ytmm.mvp worker
"""
import argparse, logging, sys, time
from pathlib import Path
from rich.console import Console
from rich.markup import escape
from . import config, status
from .config import Config
from .library import Library, parse_id
from .plan import file_name
from .worker import Worker

console = Console(highlight=False)

STIGNORE = {
    'library': '.*.tmp\n',
    'music':   '.*.part\n.*.tmp\n',
}


class output:
    def section(*values, **kwargs):
        console.print('[cyan]::', *values, **kwargs)
    def status(*values, **kwargs):
        console.print('', *values, **kwargs)
    def error(*values, **kwargs):
        console.print('[red]error[/]:', *values, **kwargs)


def ago(t: float) -> str:
    s = int(time.time() - t)
    if s < 60:   return f'{s}s ago'
    if s < 3600: return f'{s // 60} min ago'
    return f'{s // 3600} h ago'


def find(lib: Library, query: str) -> list[str]:
    id = parse_id(query)
    if id and id in lib.songs:
        return [id]
    q = query.lower()
    return [id for id, s in lib.live().items()
            if q in s.get('title', '').lower() or any(q in a.lower() for a in s.get('artists', []))]


def cmd_init(args):
    root = Path(args.root).expanduser().resolve()
    cfg = Config(root=root, role=args.role)
    if args.name:
        cfg.device = args.name
    for sub, ignore in STIGNORE.items():
        d = root / sub
        d.mkdir(parents=True, exist_ok=True)
        if not (d / '.stignore').exists():
            (d / '.stignore').write_text(ignore, encoding='utf-8')
    cfg.songs_dir.mkdir(parents=True, exist_ok=True)
    path = config.save(cfg)
    output.section(f'wrote [green1]"{escape(str(path))}"[/]')
    output.status(f'device [b]{escape(cfg.device)}[/] ({cfg.role}), root [green1]"{escape(str(root))}"[/]')
    output.status('share these folders with Syncthing:')
    output.status(f'  ytmm-library  {escape(str(cfg.library_dir))}  (Send & Receive)')
    mode = 'Send Only' if cfg.role == 'server' else 'Receive Only'
    output.status(f'  ytmm-music    {escape(str(cfg.music_dir))}  ({mode})')


def cmd_add(cfg, lib, args):
    id = parse_id(args.url)
    if not id:
        return output.error(f'not a video id or URL: {escape(args.url)}')
    artists = [a.strip() for a in args.artist.split(',')] if args.artist else None
    lib.put(id, title=args.title, artists=artists)
    lib.save()
    output.status(f'[green]added[/] {escape(file_name(id, lib.songs[id]))}')


def cmd_edit(cfg, lib, args):
    ids = find(lib, args.song)
    if len(ids) != 1:
        return output.error(f'"{escape(args.song)}" matches {len(ids)} songs, use the video id')
    artists = [a.strip() for a in args.artist.split(',')] if args.artist else None
    lib.put(ids[0], title=args.title, artists=artists)
    lib.save()
    output.status(f'[green]edited[/] {escape(file_name(ids[0], lib.songs[ids[0]]))}')


def cmd_rm(cfg, lib, args):
    ids = find(lib, args.song)
    if not ids:
        return output.error(f'no song matches "{escape(args.song)}"')
    if len(ids) > 1 and not args.all:
        output.error(f'"{escape(args.song)}" matches {len(ids)} songs (pass --all to remove them all):')
        for id in ids:
            output.status(escape(file_name(id, lib.songs[id])))
        return
    for id in ids:
        output.status(f'[red]removed[/] {escape(file_name(id, lib.songs[id]))}')
        lib.delete(id)
    lib.save()


def cmd_ls(cfg, lib, args):
    st = status.read(cfg.status_file) or {}
    failed, pending = st.get('failed', {}), set(st.get('pending', []))
    local = set(p.name for p in cfg.songs_dir.glob('*')) if cfg.songs_dir.exists() else set()
    ids = find(lib, args.query) if args.query else list(lib.live())
    for id in sorted(ids, key=lambda i: file_name(i, lib.songs[i]).lower()):
        name = file_name(id, lib.songs[id])
        if name in local:   mark = '[green]✓[/]'
        elif id in failed:  mark = '[red]✗[/]'
        elif id in pending: mark = '[yellow]…[/]'
        else:               mark = '[dim]?[/]'
        output.status(mark, escape(name), f'[red]{escape(failed[id])}[/]' if id in failed else '')
    if args.deleted:
        for id, s in sorted(lib.songs.items()):
            if s.get('deleted'):
                output.status(f'[dim]- {id} deleted by {escape(s.get("updated_by", "?"))} {ago(s["updated"])}[/]')
    output.status('[dim]✓ on this device   … server pending   ✗ failed   ? not synced yet[/]')


def cmd_status(cfg, lib, args):
    output.section(f'this device: [b]{escape(cfg.device)}[/] ({cfg.role}), '
                   f'{len(lib.live())} songs, {len(lib.songs) - len(lib.live())} tombstones')
    if lib.merged_conflicts:
        output.status(f'merged {len(lib.merged_conflicts)} conflict copies')
    st = status.read(cfg.status_file)
    if not st:
        return output.section('no status.json yet (is the worker running and synced?)')
    output.section(f'server [b]{escape(st["server"])}[/] last seen {ago(st["seen"])} · '
                   f'{st["songs"]} songs, {st["files"]} files')
    output.section(f'{len(st["pending"])} pending, {len(st["failed"])} failed')
    for id, err in st['failed'].items():
        output.status(f'[red]✗[/] {id}: {escape(err)}')
    if st.get('blocked'):
        output.status(f'[red]blocked:[/] {escape(st["blocked"])}')


def cmd_worker(cfg, args):
    if cfg.role != 'server':
        output.error('the worker only runs on a device with role = "server"')
        return
    w = Worker(cfg)
    if args.once:
        for line in w.run_once():
            output.status(escape(line))
        return
    output.section(f'worker watching [green1]"{escape(str(cfg.library_dir))}"[/] '
                   f'every {cfg.poll_interval:g}s (ctrl+c to stop)')
    try:
        w.run(on_log=lambda line: output.status(f'[dim]{time.strftime("%H:%M:%S")}[/]', escape(line)))
    except KeyboardInterrupt:
        pass


def create_parser():
    parser = argparse.ArgumentParser(description='ytmm sync MVP (fake downloads)')
    sub = parser.add_subparsers(metavar='SUBCOMMAND', dest='command')

    p = sub.add_parser('init', help='write this device\'s config and create the folders')
    p.add_argument('root', help='folder holding library/ and music/ (e.g. ~/Music/ytmm)')
    p.add_argument('--role', choices=config.ROLES, default='client')
    p.add_argument('--name', help='device name (default: hostname)')

    p = sub.add_parser('add', help='add a song by video id or URL')
    p.add_argument('url')
    p.add_argument('-t', '--title')
    p.add_argument('-a', '--artist', help='comma-separated list')

    p = sub.add_parser('edit', help='change a song\'s title or artists')
    p.add_argument('song', help='video id or text matching one song')
    p.add_argument('-t', '--title')
    p.add_argument('-a', '--artist', help='comma-separated list')

    p = sub.add_parser('rm', help='remove songs (leaves a tombstone)')
    p.add_argument('song', help='video id or text matching title/artist')
    p.add_argument('--all', action='store_true', help='remove every match')

    p = sub.add_parser('ls', help='list songs and whether this device has the file')
    p.add_argument('query', nargs='?')
    p.add_argument('-d', '--deleted', action='store_true', help='also list tombstones')

    sub.add_parser('status', help='show the server\'s status.json')

    p = sub.add_parser('worker', help='server: watch the library and "download" songs')
    p.add_argument('--once', action='store_true', help='run a single pass and exit')
    return parser


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    logging.basicConfig(stream=sys.stdout)
    parser = create_parser()
    args = parser.parse_args()
    if args.command is None:
        return parser.print_usage()
    if args.command == 'init':
        return cmd_init(args)
    try:
        cfg = config.load()
    except (FileNotFoundError, ValueError) as e:
        return output.error(escape(str(e)))
    if args.command == 'worker':
        return cmd_worker(cfg, args)
    lib = Library(cfg.library_dir, cfg.device).load()
    {'add': cmd_add, 'edit': cmd_edit, 'rm': cmd_rm, 'ls': cmd_ls, 'status': cmd_status}[args.command](cfg, lib, args)


if __name__ == '__main__':
    main()
