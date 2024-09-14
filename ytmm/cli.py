import argparse
import logging
import sys
from .ytmm import YoutubeMM

def create_parser():
    parser = argparse.ArgumentParser(description="YouTube Music Manager (v0.1.0)")
    subparsers = parser.add_subparsers(dest='command')

    # Sync command
    sync_parser = subparsers.add_parser('sync', help='sync from database to directory')
    sync_parser.add_argument('-o', '--output', type=str, default=None, help='Output directory')

    # Query command
    query_parser = subparsers.add_parser('query', help='query music from database')
    query_parser.add_argument('-i', action='store_true', help='case insensitive')
    query_parser.add_argument('-T', '--title',  metavar='PATTERN', help='pattern to filter by music title')
    query_parser.add_argument('-A', '--artist', metavar='PATTERN', help='pattern to filter by music artist')

    # Add command
    add_parser = subparsers.add_parser('add', help='add YouTube URL to database')
    add_parser.add_argument('urls', nargs='*', help='youTube URLs to add')
    #add_parser.add_argument('-t', '--title', help='override Music Title')
    #add_parser.add_argument('-a', '--artists', help='override Artists (Comma-separated list)')

    # Remove command
    remove_parser = subparsers.add_parser('rm', help='remove YouTube URL from database')
    remove_parser.add_argument('-i', action='store_true', help='case insensitive')
    remove_parser.add_argument('-A', '--artist', metavar='PATTERN', help='pattern to filter by music artist')
    remove_parser.add_argument('pattern',  metavar='PATTERN', help='pattern to filter by music title')

    return parser

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    logging.basicConfig(stream=sys.stdout)
    parser = create_parser()
    args = parser.parse_args()
    if args.command != None:
        with YoutubeMM() as ytmm:
            if args.command == 'sync':
                ytmm.sync(args.output)
            elif args.command == 'add':
                #title = args.title
                #artists = [s.strip() for s in args.artists.split(',')] if args.artists else None
                ytmm.add(args.urls)
            elif args.command == 'query':
                title_pattern  = '(?i)' + args.title  if args.title  and args.i else args.title
                artist_pattern = '(?i)' + args.artist if args.artist and args.i else args.artist
                ytmm.query(title_pattern, artist_pattern)
            elif args.command == 'rm':
                pattern        = '(?i)' + args.pattern if args.i else args.pattern
                artist_pattern = '(?i)' + args.artist  if args.artist and args.i else args.artist
                ytmm.remove(pattern, artist_pattern)
    else:
        parser.print_usage()
