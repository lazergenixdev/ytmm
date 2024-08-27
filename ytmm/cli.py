import argparse
import logging
import sys
from .ytmm import YoutubeMM

def create_parser():
    parser = argparse.ArgumentParser(description="YouTube Music Manager (ytmm)")
    subparsers = parser.add_subparsers(dest='command')

    # Sync command
    sync_parser = subparsers.add_parser('sync', help='Sync from database to directory')
    sync_parser.add_argument('-o', '--output', type=str, default=None, help='Output directory')

    # Add command
    add_parser = subparsers.add_parser('add', help='Add YouTube URL to database')
    add_parser.add_argument('urls', nargs='*', help='YouTube URLs to add')
    add_parser.add_argument('-t', '--title', help='Override Music Title')
    add_parser.add_argument('-a', '--artists', help='Override Artists (Comma-separated list)')

    # Remove command
    remove_parser = subparsers.add_parser('rm', help='Remove YouTube URL from database')
    remove_parser.add_argument('url', type=str, help='YouTube URL to remove')

    return parser

def main():
    logging.basicConfig(stream=sys.stdout)
    parser = create_parser()
    args = parser.parse_args()
    if args.command != None:
        with YoutubeMM() as ytmm:
            if args.command == 'sync':
                ytmm.sync(args.output)
            elif args.command == 'add':
                title = args.title
                artists = [s.strip() for s in args.artists.split(',')] if args.artists else None
                ytmm.add(args.urls)
            elif args.command == 'rm':
                ytmm.remove(args.url)
    else:
        parser.print_help()
