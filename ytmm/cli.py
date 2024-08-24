import argparse

def create_parser():
    parser = argparse.ArgumentParser(description="YouTube Music Manager (ytmm)")
    subparsers = parser.add_subparsers(dest='command')

    # Sync command
    sync_parser = subparsers.add_parser('sync', help='Sync from database to directory')
    sync_parser.add_argument('-o', '--output', type=str, default='out/', help='Output directory')

    # Add command
    add_parser = subparsers.add_parser('add', help='Add YouTube URL to database')
    add_parser.add_argument('url', type=str, help='YouTube URL to add')

    # Remove command
    remove_parser = subparsers.add_parser('rm', help='Remove YouTube URL from database')
    remove_parser.add_argument('url', type=str, help='YouTube URL to remove')

    return parser

def main():
    parser = create_parser()
    args = parser.parse_args()
    print(f"Command: {args.command}")
    if args.command == 'sync':
        print(f"Output directory: {args.output}")
    elif args.command == 'add':
        print(f"URL to add: {args.url}")
    elif args.command == 'rm':
        print(f"URL to remove: {args.url}")
    else:
        parser.print_help()

if __name__ == '__main__':
    main()

