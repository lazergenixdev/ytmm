from argparse import ArgumentParser, BooleanOptionalAction
import logging
import sys
from .ytmm import YoutubeMM

def add_filters(parser: ArgumentParser):
    parser.add_argument('-i', action='store_true', help='case insensitive')
    parser.add_argument('-T', '--title',  metavar='PATTERN', help='pattern to filter by music title')
    parser.add_argument('-A', '--artist', metavar='PATTERN', help='pattern to filter by music artist')

def add_playlist_subcommands(parser: ArgumentParser):
    subparsers = parser.add_subparsers(dest='playlist_command', required=True)

    sync_parser = subparsers.add_parser('sync', help='download music from playlists')
    sync_parser.add_argument('playlist', nargs='?', help='playlist name pattern')
    
    add_parser = subparsers.add_parser('add', help='add music to playlist')
    add_parser.add_argument('playlist', help='playlist name')
    add_parser.add_argument('-D', '--downloaded', action=BooleanOptionalAction, help='add [not] downloaded music')
    add_parser.add_argument('-f', '--first', type=int, help='add the first N songs')
    add_parser.add_argument('-l', '--last', type=int, help='add the last N songs')
    add_filters(add_parser)
    
    remove_parser = subparsers.add_parser('rm', help='remove music from playlist')
    remove_parser.add_argument('playlist', help='playlist name')
    remove_parser.add_argument('-D', '--downloaded', action=BooleanOptionalAction, help='add [not] downloaded music')
    remove_parser.add_argument('-f', '--first', type=int, help='add the first N songs')
    remove_parser.add_argument('-l', '--last', type=int, help='add the last N songs')
    add_filters(remove_parser)
    
    query_parser = subparsers.add_parser('query', help='list music in playlist')
    query_parser.add_argument('-F', '--files', action='store_true', help='list files')
    query_parser.add_argument('playlist', help='playlist name')

    subparsers.add_parser('list', help='list all playlists')

def create_parser():
    parser = ArgumentParser(description="YouTube Music Manager (v0.2.0)")
    subparsers = parser.add_subparsers(metavar="SUBCOMMAND", dest='command')

    # Sync command
    sync_parser = subparsers.add_parser('sync', aliases=['s'], help='sync from database to directory')
    sync_parser.add_argument('-o', '--output', type=str, default=None, help='Output directory')
    add_filters(sync_parser)

    # Query command
    query_parser = subparsers.add_parser('query', aliases=['q'], help='query music from database')
    query_parser.add_argument('-D', '--downloaded', action=BooleanOptionalAction, help='only show [not] downloaded music')
    query_parser.add_argument('-F', '--files', action='store_true', help='list files')
    query_parser.add_argument('-f', '--first', type=int, help='only list the first N songs')
    query_parser.add_argument('-l', '--last', type=int, help='only list the last N songs')
    query_parser.add_argument('-n', '--count', action='store_true', help='show number of songs')
    add_filters(query_parser)

    # Add command
    add_parser = subparsers.add_parser('add', aliases=['a'], help='add YouTube URL to database')
    add_parser.add_argument('urls', nargs='*', help='youTube URLs to add')
    #add_parser.add_argument('-t', '--title', help='override Music Title')
    #add_parser.add_argument('-a', '--artists', help='override Artists (Comma-separated list)')

    # Remove command
    remove_parser = subparsers.add_parser('rm', aliases=['r'], help='remove YouTube URL from database')
    remove_parser.add_argument('-i', action='store_true', help='case insensitive')
    remove_parser.add_argument('-A', '--artist', metavar='PATTERN', help='pattern to filter by music artist')
    remove_parser.add_argument('pattern',  metavar='PATTERN', help='pattern to filter by music title')

    # Playlist command
    playlist_parser = subparsers.add_parser('playlist', aliases=['p'], help='view/add/remove playlists')
    add_playlist_subcommands(playlist_parser)

    return parser


def run(ytmm: YoutubeMM, args):
    def list_filter(entries):
        if args.last:  return entries[-args.last:]
        if args.first: return entries[:args.first]
        return entries
    
    if 'title' in args:
        title_pattern = '(?i)' + args.title  if args.title  and args.i else args.title
    if 'artist' in args:
        artist_pattern = '(?i)' + args.artist if args.artist and args.i else args.artist
    
    match args.command[0]:
        case 's':
            ytmm.sync(args.output, title_pattern, artist_pattern)
        case 'a':
            #title = args.title
            #artists = [s.strip() for s in args.artists.split(',')] if args.artists else None
            ytmm.add(args.urls)
        case 'q':
            if args.count:
                ytmm.count()
            else:
                ytmm.query(title_pattern, artist_pattern, args.downloaded, args.files, list_filter)
        case 'r':
            pattern = '(?i)' + args.pattern if args.i else args.pattern
            ytmm.remove(pattern, artist_pattern)
        case 'p':
            match args.playlist_command:
                case 'sync':
                    ytmm.sync_playlists()
                case 'add':
                    ytmm.add_to_playlist(args.playlist, title_pattern, artist_pattern, args.downloaded, list_filter)
                case 'rm':
                    ytmm.remove_playlist(args.playlist, title_pattern, artist_pattern, args.downloaded, list_filter)
                case 'query':
                    ytmm.query_playlist(args.playlist, args.files)
                case 'list':
                    ytmm.list_playlists()


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    logging.basicConfig(stream=sys.stdout)
    parser = create_parser()
    args = parser.parse_args()
    if args.command != None:
        with YoutubeMM() as ytmm:
            run(ytmm, args)
    else:
        parser.print_usage()
