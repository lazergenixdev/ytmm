import logging, json, os, shutil, re
import concurrent.futures
import yt_dlp
from .utils import (
    file_name_from_title,
    parse_title,
    filter_entries,
)
from rich.markup import escape
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.progress import (
    Progress,
    SpinnerColumn,
    TimeElapsedColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
)
from collections.abc import Callable

DEFAULT_DATABASE = 'music.json'
DEFAULT_ROOT     = 'music'

console = Console(highlight=False)

def line_text(text: str, width: int) -> str:
    k = max(width,0) - len(text) - 2
    left  = '-'*(k//2)
    right = '-'*(k-k//2)
    return f'{left} {text} {right}'

"""
Entry:
    'id':      str,
    'title':   str,
    'artists': list[str],
    'album':   str,       [optional]
    'year':    int,       [optional]
    'path':    str        [optional] (defaults to root)
"""


class output:
    def section(*values, **kwargs):
        console.print('[cyan]::', *values, **kwargs)
    def status(*values, **kwargs):
        console.print('', *values, **kwargs)
    def error(*values, **kwargs):
        console.print('[red]error[/]:', *values, **kwargs)
    def path(p):
        return f'[green1]"{escape(p)}"[/green1]'
    def ask(q):
        return Confirm.ask(f'[cyan]::[/] {q}', default=True)
    def ask_all(q):
        return Prompt.ask (
            fr'[cyan]::[/] {q} [prompt.choices]\[y/n/A]',
            choices=['y','n','a','Y','N','A'],
            default='A',
            show_choices=False
        ).lower()




def find_database(database):
    dirs = []
    for file in os.listdir('.'):
        if os.path.isdir(file):
            if file != DEFAULT_ROOT:
                dirs.append(file)
        else:
            if file == DEFAULT_DATABASE:
                return file

    for root in dirs:
        for file in os.listdir(root):
            path = os.path.join(root, file)
            if os.path.isdir(path): continue
            if file == DEFAULT_DATABASE:
                return path

    return None

def print_entries(entries):
    from rich.table import Column, Table
    from rich import box
    import time

    CURRENT_YEAR = time.localtime().tm_year

    def mapf(minv, maxv, var):
        x = (var - minv) / (maxv - minv)
        return min(max(x, 0), 1)

    def year_color(year: int) -> str:
        if year >= CURRENT_YEAR-5:
            x = int(mapf(CURRENT_YEAR-5, CURRENT_YEAR, year)*255)
            return f'[rgb(255,{255-x},0)]'
        if year >= CURRENT_YEAR-30:
            x = int(mapf(CURRENT_YEAR-30, CURRENT_YEAR-5, year)*255)
            return f'[rgb({x},255,{255-x})]'
        else:
            return '[rgb(0,255,255)]'

    table = Table(
        Column(header="ID",      style="grey39",               no_wrap=True, min_width=11),
        Column(header="Year",    justify='center',             no_wrap=True, min_width=4),
        Column(header="Artists", style="italic orchid1",       no_wrap=True, ratio=2),
        Column(header="Title",   style="medium_spring_green",  no_wrap=True, ratio=3),
        box=None,
        expand=True
    )
    
    for i, entry in enumerate(entries):
        year = f'{year_color(entry['year'])}{entry['year']}' if 'year' in entry else '--'
        artists = ', '.join(entry['artists'])
        table.add_row(entry['id'], year, artists, entry['title'])

    console.print(table)




class ProgressTracker:
    def __init__(self, n, progress: Progress):
        self.progress = progress
        self.n = n
        self.errors = []
    
    def save_error(self, error):
        self.errors.append(error)




class YoutubeMM:
    def __init__(self, database=DEFAULT_DATABASE):
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        self.file = find_database(database)
        self.modified = False
        #self.logger.info("database file: %s", database_file)

    def __enter__(self):
        self.load()
        return self
    
    def __exit__(self, *args):
        if self.modified:
            self.save_to(self.file)




    def sync (
        self,
        output_dir: str | None,
        title_pattern: str | None,
        artist_pattern: str | None
    ) -> None:
        # TODO: audit code

        if output_dir:
            self.root = output_dir

        output.section('Syncronizing music files...')

        # Here we need to download everything
        if not os.path.exists(self.root):
            output.status(f'root {output.path(self.root)} not found')
            
            if not output.ask(f"Create new [b]root[/b] directory?"):
                return
            
            output.status("creating directory...")
            os.mkdir(self.root)

        filenames = []
        ids = []
        id_to_index = dict()

        for i, entry in enumerate(self.entries):
            filenames.append(file_name_from_title(entry['title']))
            ids.append(entry['id'])
            id_to_index[entry['id']] = i

        from pathlib import Path

        # Find files that do not belong
        for root, folders, files in os.walk(self.root):
            for f in files:
                stem = Path(f).stem
                if stem not in filenames:
                    # Check if music was downloaded but needs renaming
                    if os.path.splitext(f)[1] == '.mp3' and stem in id_to_index:
                        entry = self.entries[id_to_index[stem]]
                        self._rename_entry(entry)
                        output.status('[cyan]repaired', escape(stem), '=>', escape(entry['title']))
                        continue
                    
                    if Confirm.ask(f'remove [red]"{escape(f)}"[/]?'):
                        os.remove(os.path.join(root, f))

        # Filter by given patterns 
        filtered = filter_entries(self.entries, title_pattern, artist_pattern)

        entries = []
        # Only download what does not exist
        for entry in filtered:
            file_name = f"{file_name_from_title(entry['title'])}.mp3"
            path = os.path.join(self.root, file_name)

            if not os.path.exists(path):
                output.status('[red]missing', f'[i]{escape(entry['title'])}')
                entries.append(entry)

        if not entries:
            output.status("nothing to do")
            return

        print()
        output.section("Music to download:")
        for entry in entries:
            output.status(escape(file_name_from_title(entry['title'])), end=' ')
        print('\n')

        if not output.ask("Proceed to download?"): return

        self.download(entries)




    def add(self, urls: list):
        def find(s: str):
            for u, url in enumerate(urls):
                if s in url:
                    return u, url
            return None, None

        output.status("looking for duplicates...")

        yes_to_all = False
        replace = [-1 for _ in urls]
        for i, entry in enumerate(self.entries):
            j, url = find(entry['id'])
            if j != None:
                output.status(f'found [u orange1]{escape(url)}[/] as [green1]"{escape(entry['title'])}"')

                if yes_to_all:
                    replace[j] = i
                    continue

                match output.ask_all("Replace existing?"):
                    case 'y': replace[j] = i
                    case 'n': replace[j] = None
                    case 'a': replace[j] = i; yes_to_all = True

        download_list = []
        # None -> remove
        for i,r in enumerate(replace):
            if r != None:
                download_list.append((urls[i], r))

        if not download_list: return

        output.section("Downloading music...")

        def download(url: str, task_id, index: int):
            progress.update(task_id, visible=True)
            info = d.extract_info(url, download=True, extra_info={'ytmm_task_id': task_id})
            new_entry = _info_to_entry(info)
            self._rename_entry(new_entry)
        
            if index >= 0:
                self.entries[index] = new_entry
            else:
                self.entries.append(new_entry)

            self.modified = True
            progress.update(task_id, advance=1)
            
        with Progress (
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(None),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            expand=True
        ) as progress:
            tracker = ProgressTracker(len(download_list), progress)
            with self.downloader(tracker) as d:
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                    for url, index in download_list:
                        task_id = progress.add_task(url, start=False, total=None, visible=False)
                        executor.submit(download, url, task_id, index)
                    total_taskid = progress.add_task('-- Total --', total=None)
                    tracker.totalid = total_taskid
                    executor.shutdown()
                tracker.progress.update(tracker.totalid, completed=100)
        
        for error in tracker.errors:
            output.error(escape(error.replace('ERROR: ','')))




    def query (
        self,
        title_pattern:  str  | None = None,
        artist_pattern: str  | None = None,
        downloaded:     bool | None = None,
        files:          bool = None,
        custom_filter:  Callable[[dict], dict] | None = None
    ) -> None:
        # TODO: Print less info if terminal width is small (Title > Artists > Year > ID)

        if downloaded is not None:
            filtered = list(filter(lambda x: self.is_downloaded(x) == downloaded, self.entries))
        else:
            filtered = self.entries
        filtered = filter_entries(filtered, title_pattern, artist_pattern)
        
        if custom_filter:
            filtered = custom_filter(filtered)

        if files:
            for entry in filtered:
                print(self.entry_path(entry))
            return
        
        print_entries(filtered)




    def count(self) -> None:
        from rich.table import Column, Table
        from rich import box

        table = Table(
            Column(header="Total ",          style="dodger_blue1", no_wrap=True),
            Column(header="Downloaded ",     style='green1',       no_wrap=True),
            Column(header="Not Downloaded ", style="orange_red1",  no_wrap=True),
            title_style='italic',
            box=None,
        )
        
        total = len(self.entries)

        def download_mask(entry):
            if os.path.isfile(self.entry_path(entry)):
                return 1
            return 0
        
        downloaded = sum(download_mask(entry) for entry in self.entries)
        table.add_row(str(total), str(downloaded), str(total-downloaded))

        console.print(table)




    def remove(self, title_pattern: str, artist_pattern: str | None):
        output.status("looking for music...")

        filtered = filter_entries(self.entries, title_pattern, artist_pattern)
        filtered.sort(key=lambda e: e['title'].casefold())

        output.section("Music to remove:")
        for entry in filtered:
            output.status(file_name_from_title(entry['title']), end=' ')
        print('\n')
        
        if not output.ask("Proceed?"): return

        # TODO: This is very dumb! FIX
        def keep(entry):
            for f in filtered:
                if entry['id'] == f['id']:
                    file_name = file_name_from_title(entry['title']) + '.mp3'
                    path = os.path.join(self.root, file_name)
                    if os.path.exists(path):
                        os.remove(path)
                        output.status(f"removed {file_name}...")
                    return False
            return True

        self.entries = list(filter(keep, self.entries))
        self.modified = True




    def sync_playlists (self) -> None:
        if not os.path.exists('playlists'):
            output.status('playlist directory not found')
            
            if not output.ask(f"Create new [b]playlist[/b] directory?"):
                return
            
            output.status("creating directory...")
            os.mkdir('playlists')

        for name, playlist in self.playlists.items():
            def playlist_entries(playlist):
                filtered = []
                for entry in self.entries:
                    if entry['id'] in playlist and not self.is_downloaded(entry):
                        filtered.append(entry)
                return filtered
            
            need_download = playlist_entries(playlist)

            if len(need_download) > 0:
                output.status(f'[green1]"{name}"', 'has music that needs downloading')
            
            self.save_playlist_to(f'playlists/{name}.m3u', playlist)


            

    def add_to_playlist (
        self,
        name: str, 
        title_pattern:  str  | None = None,
        artist_pattern: str  | None = None,
        downloaded:     bool | None = None,
        custom_filter:  Callable[[dict], dict] | None = None
    ) -> None:
        playlist = self.playlists[name] if name in self.playlists else []

        filtered = []

        for entry in self.entries:
            if entry['id'] not in playlist:
                filtered.append(entry)
        
        if downloaded is not None:
            filtered = list(filter(lambda x: self.is_downloaded(x) == downloaded, filtered))

        filtered = filter_entries(filtered, title_pattern, artist_pattern)
        
        if custom_filter:
            filtered = custom_filter(filtered)

        if len(filtered) == 0:
            output.status('nothing to do')
            return

        output.section('Music to add:')
        for i, entry in enumerate(filtered):
            output.status(f'[i]{escape(entry['title'])}')
        print()
        
        if not output.ask(f'Add to playlist [medium_spring_green]"{escape(name)}"'):
            return
        
        output.status('adding music to playlist...')
        [playlist.append(entry['id']) for entry in filtered]

        output.status('updating playlist...')
        self.playlists[name] = playlist
        self.modified = True




    def remove_playlist (
        self,
        name: str, 
        title_pattern:  str  | None = None,
        artist_pattern: str  | None = None,
        downloaded:     bool | None = None,
        custom_filter:  Callable[[dict], dict] | None = None
    ) -> None:
        if name not in self.playlists:
            output.status(f'playlist [medium_spring_green]"{escape(name)}"[/] does not exist')
            return

        playlist = self.playlists[name]
        filtered = []

        for entry in self.entries:
            if entry['id'] in playlist:
                filtered.append(entry)
        
        if downloaded is not None:
            filtered = list(filter(lambda x: self.is_downloaded(x) == downloaded, filtered))

        filtered = filter_entries(filtered, title_pattern, artist_pattern)
        
        if custom_filter:
            filtered = custom_filter(filtered)
        
        if len(filtered) == 0:
            output.status('nothing to do')
            return

        output.section('Music to remove:')
        for i, entry in enumerate(filtered):
            output.status(f'[i]{escape(entry['title'])}')
        print()
        
        if not output.ask(f'Remove from playlist [medium_spring_green]"{escape(name)}"'):
            return
        
        output.status('removing music from playlist...')
        filtered = [entry['id'] for entry in filtered]
        playlist = list(filter(lambda x: x not in filtered, playlist))

        output.status('updating playlist...')
        self.playlists[name] = playlist
        self.modified = True
        



    def query_playlist (
        self,
        name: str,
        files: bool = False,
    ) -> None:
        if name not in self.playlists:
            output.status(f'playlist [medium_spring_green]"{escape(name)}"[/] does not exist')
            return

        playlist = self.playlists[name]
        filtered = []

        for entry in self.entries:
            if entry['id'] in playlist:
                filtered.append(entry)

        if files:
            for entry in filtered:
                print(self.entry_path(entry))
            return

        print_entries(filtered)
        



    def list_playlists (self) -> None:
        from rich.table import Column, Table
        from rich import box

        table = Table(
            Column(header="Count",     style='blue',   no_wrap=True),
            Column(header="Playlist",  style='green',  no_wrap=True, ratio=1),
            Column(header="Music",     style='cyan',    no_wrap=True, ratio=4),
            box=None,
            expand=True
        )

        def playlist_titles(playlist):
            filtered = []
            for entry in self.entries:
                if entry['id'] in playlist:
                    filtered.append(f'"{escape(entry['title'])}"')
            return filtered
        
        for name, playlist in self.playlists.items():
            music = ', '.join(playlist_titles(playlist))
            table.add_row(str(len(playlist)), name, music)

        console.print(table)



    def download(self, entries):
        output.section("Retrieving music...")

        def download(i, task_id):
            entry = entries[i]
            extra = {'ytmm_task_id': task_id, 'index': i}
            progress.update(task_id, visible=True)
            d.extract_info(entry['id'], download=True, extra_info=extra)
            self._rename_entry(entry)
            progress.update(task_id, advance=1)

        with Progress (
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(None),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            expand=True
        ) as progress:
            tracker = ProgressTracker(len(entries), progress)
            with self.downloader(tracker) as d:
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                    for i in range(len(entries)):
                        task_id = progress.add_task(entries[i]['title'], start=False, total=None, visible=False)
                        executor.submit(download, i, task_id)
                    total_taskid = progress.add_task('Total', total=None)
                    tracker.totalid = total_taskid
                    executor.shutdown()
                tracker.progress.update(tracker.totalid, completed=100)




    def load(self):
        if os.path.exists(self.file):
            try:
                db = json.load(open(self.file))

                if 'data' in db:
                    self.entries = db['data']
                else:
                    output.status("[green1]'data'[/] not found, creating empty database...")
                    self.entries = []
                    self.modified = True

                if 'root' in db:
                    self.root = db['root']
                else:
                    output.status("[green1]'root'[/] not found, using", output.file(DEFAULT_ROOT))
                    self.root = DEFAULT_ROOT
                    self.modified = True

                if 'playlists' in db:
                    self.playlists = db['playlists']
                else:
                    output.status("[green1]'playlists'[/] not found, creating empty playlist container...")
                    self.playlists = dict()
                    self.modified = True

            except Exception as e:
                output.error("Failed to load database")
                output.error(e)
                exit(1)
        else:
            output.status(output.file(self.file), "not found, creating new database...")
            self.entries  = []
            self.root     = DEFAULT_ROOT
            self.modified = True




    def save_to(self, file):
        with open(file, "w") as f:
            output.section("Saving changes...")
            try:
                json.dump({'root': self.root, 'data': self.entries, 'playlists': self.playlists}, f, indent=4)
                output.status('wrote to database', output.path(self.file))
            except Exception as e:
                output.error(f'Failed to write to database file ({escape(e)})')




    def save_playlist_to(self, file, playlist: list[str]):
        music_files = []
        entry_id_map = dict()
        for entry in self.entries:
            entry_id_map[entry['id']] = entry

        for _id in playlist:
            music_files.append('../' + self.entry_path(entry_id_map[_id]) + '\n')

        with open(file, "w") as f:
            try:
                f.write('#EXTM3U\n\n')
                f.writelines(music_files)
                output.status('wrote to playlist', output.path(file))
            except Exception as e:
                output.error(f'Failed to write to playlist file ({escape(e)})')




    def _rename_entry(self, entry):
        _from = f"{entry['id']}.mp3"
        _to   = f"{file_name_from_title(entry['title'])}.mp3"
        _from = os.path.join(self.root, _from)
        _to   = os.path.join(self.root, _to)
        shutil.move(_from, _to)

    def entry_path(self, entry):
        return os.path.join(self.root, f"{file_name_from_title(entry['title'])}.mp3")
    
    def is_downloaded(self, entry):
        return os.path.isfile(self.entry_path(entry))

    def downloader(self, tracker: ProgressTracker):
        class MyLogger:
            def debug(self, msg):
                # For compatibility with youtube-dl, both debug and info are passed into debug
                # You can distinguish them by the prefix '[debug] '
                if not msg.startswith('[debug] '):
                    return self.info(msg)
                pass

            def info(self, msg: str):
                pass

            def warning(self, msg):
                pass

            def error(self, msg):
                tracker.save_error(msg)

        def progress_hook(d):
            self.progress_hook(tracker, d)

        return yt_dlp.YoutubeDL({
            'concurrent_fragment_downloads': 8,
            'logger': MyLogger(),
            'progress_hooks': [progress_hook] if tracker else [],
            'extract_flat': 'discard_in_playlist',
            'final_ext': 'mp3',
            'format': 'bestaudio/best',
            'fragment_retries': 10,
            'ignoreerrors': 'only_download',
            'outtmpl': {
                'default': os.path.join(self.root,'%(id)s.%(ext)s'),
            },
            'postprocessors': [
                {
                    'key': 'FFmpegExtractAudio',
                    'nopostoverwrites': False,
                    'preferredcodec': 'mp3',
                    'preferredquality': '5',
                },
                {
                    'key': 'FFmpegMetadata',
                    'add_chapters': True,
                    'add_infojson': 'if_exists',
                    'add_metadata': True,
                },
                {
                    'key': 'FFmpegConcat',
                    'only_multi_video': True,
                    'when': 'playlist',
                }
            ],
            'retries': 10,
        })
    
    """
    progress_hooks:    A list of functions that get called on download
                       progress, with a dictionary with the entries
                       * status: One of "downloading", "error", or "finished".
                                 Check this first and ignore unknown values.
                       * info_dict: The extracted info_dict
  
                       If status is one of "downloading", or "finished", the
                       following properties may also be present:
                       * filename: The final filename (always present)
                       * tmpfilename: The filename we're currently writing to
                       * downloaded_bytes: Bytes on disk
                       * total_bytes: Size of the whole file, None if unknown
                       * total_bytes_estimate: Guess of the eventual file size,
                                               None if unavailable.
                       * elapsed: The number of seconds since download started.
                       * eta: The estimated time in seconds, None if unknown
                       * speed: The download speed in bytes/second, None if
                                unknown
                       * fragment_index: The counter of the currently
                                         downloaded video fragment.
                       * fragment_count: The number of fragments (= individual
                                         files that will be merged)
  
                       Progress hooks are guaranteed to be called at least once
                       (with status "finished") if the download is successful.
    """
    def progress_hook(self, tracker: ProgressTracker, d: dict):
        task_id = d['info_dict']['ytmm_task_id']
        if d['status'] == 'downloading':
            entry = _info_to_entry(d['info_dict'])
            downloaded = d['downloaded_bytes']
            total      = d['total_bytes']
            tracker.progress.start_task(task_id)
            tracker.progress.update(task_id, completed=downloaded, total=total+1, description=entry['title'])
            if tracker.totalid is not None:
                p = sum(t.percentage for t in tracker.progress.tasks[:-1])/tracker.n
                max_width = max(len(t.description) if t.visible else 0 for t in tracker.progress.tasks)
                tracker.progress.update (
                    tracker.totalid,
                    description=line_text('Total', max_width),
                    completed=p,
                    total=100
                )
        elif d['status'] == 'finished':
            pass
        elif d['status'] == 'error':
            tracker.progress.remove_task(task_id)

    
# (debug help) python -m yt_dlp ID --no-download --write-info-json
def _info_to_entry(info: dict):
    new_entry = {'id': info['id']}
    if 'track' in info:
        new_entry['title']   = info['track']
        new_entry['artists'] = info['artists']
        new_entry['album']   = info['album']
        if info['release_year'] is not None:
            new_entry['year'] = info['release_year']
        else: # Try and find the year
            description: str = info['description']
            if description.startswith('Provided to YouTube'):
                match = re.search(r"Released on: (?P<year>\d{4}).\d{2}.\d{2}", description)
                if match:
                    new_entry['year'] = int(match.group('year'))
    else:
        new_entry['artists'], new_entry['title'] = parse_title(info['title'])
    return new_entry
