import logging
import json
import os
import shutil
import concurrent.futures
import yt_dlp
#import ffmpeg
from wcwidth import wcswidth as width
from re import compile as regex
from pathlib import Path
from . import output
from .utils import (
    file_name_from_title,
    parse_title,
)
from rich.progress import (
    Progress,
    SpinnerColumn,
    TimeElapsedColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
)
from rich.prompt import Confirm
import time

DEFAULT_DATABASE = 'music.json'
DEFAULT_ROOT     = 'music'

"""
Entry:
    'id':      str,
    'title':   str,
    'artists': list[str], [optional]
    'album':   str,       [optional]
    'year':    int,       [optional]
    'path':    str        [optional] (defaults to root)
"""

def filter_entries(entries, title_pattern: str | None, artist_pattern: str | None):
    if not (title_pattern or artist_pattern):
        return entries
    
    filtered = []
    re_title  = regex(title_pattern)  if title_pattern else None
    re_artist = regex(artist_pattern) if artist_pattern else None
    for entry in entries:
        add = False
        if title_pattern:
            if re_title.search(entry['title']):
                add = True
        else: 
            add = True
        
        if artist_pattern:
            add_artist = False
            for a in entry['artists']:
                if re_artist.search(a):
                    add_artist = True
                    break
            add = add and add_artist
        
        if add:
            filtered.append(entry)
    return filtered


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

class ProgressTracker:
    def __init__(self, n, progress: Progress):
        self.progress = progress
        self.n = n
        self.errors = []
    
    def save_error(self, error):
        self.errors.append(error)

#class Task:
#    def __init__(self)
#        self.task_id = Progress.Tas


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


    def sync(self, output_dir: str | None, title_pattern: str | None, artist_pattern: str | None):
        if output_dir:
            self.root = output_dir
        output.section(f'Syncronizing music files...')

        # Here we need to download everything
        if not os.path.exists(self.root):
            output.status(f'root {output.file(self.root)} not found')
            
            if not Confirm.ask(f"Create new [b]root[/b] directory?", default=True):
                return
            
            output.status("creating directory...")
            os.mkdir(self.root)

            self.download(self.entries)
            return

        filenames = []
        ids = []
        id_to_index = dict()

        for i, entry in enumerate(self.entries):
            filenames.append(file_name_from_title(entry['title']))
            ids.append(entry['id'])
            id_to_index[entry['id']] = i

        # Find files that do not belong
        for root, folders, files in os.walk(self.root):
            for f in files:
                stem = Path(f).stem
                if stem not in filenames:
                    # Check if music was downloaded but needs renaming
                    if os.path.splitext(f)[1] == '.mp3' and stem in id_to_index:
                        entry = self.entries[id_to_index[stem]]
                        self._rename_entry(entry)
                        output.status(output.color.blue("repaired"), stem, '=>', entry['title'])
                        continue
                    
                    if output.ask(f'Remove "{f}"?'):
                        os.remove(os.path.join(root, f))

        # Filter by given patterns 
        filtered = filter_entries(self.entries, title_pattern, artist_pattern)

        entries = []
        # Only download what does not exist
        for entry in filtered:
            file_name = f"{file_name_from_title(entry['title'])}.mp3"
            path = os.path.join(self.root, file_name)

            if not os.path.exists(path):
                output.status(output.color.red("missing"), file_name)
                entries.append(entry)

        if not entries:
            output.status("nothing to do")
            return

        print()
        output.section("Music to download:")
        for entry in entries:
            output.status(file_name_from_title(entry['title']), end=' ')
        print('\n')

        if not Confirm.ask("[cyan]::[/] Proceed to download?"): return

        self.download(entries)


    def add(self, urls: list):
        def find(s: str):
            for u, url in enumerate(urls):
                if s in url:
                    return u, url
            return None, None

        replace = [-1 for _ in urls]
        output.status("looking for duplicates...")
        for i, entry in enumerate(self.entries):
            j, url = find(entry['id'])
            if j != None:
                output.status(f"found {url} as {output.special(entry['title'])}")
                if output.ask("Replace existing?"):
                    replace[j] = i
                else:
                    replace[j] = None

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
                    total_taskid = progress.add_task('Total', total=None)
                    tracker.totalid = total_taskid
                    executor.shutdown()
                tracker.progress.update(tracker.totalid, completed=100)
        
            for error in tracker.errors:
                output.status(error)


    def query(self, title_pattern, artist_pattern, downloaded: bool | None = None):
        # TODO: Print less info is terminal width is small (Title > Artists > Year > ID)

        CURRENT_YEAR = time.localtime().tm_year

        def color_year(year):
            if year >= CURRENT_YEAR:
                return RED
            elif year >= CURRENT_YEAR-2:
                return ORANGE
            elif year >= CURRENT_YEAR-5:
                return YELLOW
            elif year >= CURRENT_YEAR-5:
                return WHITE

        AW = 24
        def j(s,n):
            if width(s) > n:
                w = [width(c) for c in s]
                end = 0
                for i,l in enumerate(w):
                    end += l
                    if end > n-2:
                        return s[0:i] + '..';
            return s + ' ' * max(0, n-width(s))
        def s(i,e):
            return "{5:>4}  {0}{4}{3}{4}{1}{4}{2:<50}".format(e['id'],j(', '.join(e['artists']),AW),e['title'],e['year'] if 'year' in e else '    ','   ',i)
        #self.entries.sort(key=lambda e: e['artists'][0].casefold())

        def downloaded_eq(value: bool):
            def fn(entry):
                return os.path.isfile(self.entry_path(entry)) == value
            return fn

        if downloaded is not None:
            filtered = list(filter(downloaded_eq(downloaded), self.entries))
        else:
            filtered = self.entries
        filtered = filter_entries(filtered, title_pattern, artist_pattern)
        filtered.sort(key=lambda e: int(e['year']) if 'year' in e else 0)

        # header
        #print(s('',{'id': 'id'.ljust(11), 'artists': ['artists'], 'title': 'title', 'year': 'year'}))
        #print(s('',{'id': '-' * 11, 'artists': ['-' * AW], 'title': '-'*50, 'year': '-'*4}))
        # body
        [print(s(i+1,entry)) for i,entry in enumerate(filtered)]


    def remove(self, title_pattern: str, artist_pattern: str | None):
        output.status("looking for music...")

        filtered = filter_entries(self.entries, title_pattern, artist_pattern)
        filtered.sort(key=lambda e: e['title'].casefold())

        output.section("Music to remove:")
        for entry in filtered:
            output.status(file_name_from_title(entry['title']), end=' ')
        print('\n')
        
        if not output.ask("Proceed?"): return

        # This is dumb
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
                    output.status("'data' not found, creating empty database...")
                    self.entries = []
                    self.modified = True
                if 'root' in db:
                    self.root = db['root']
                else:
                    output.status("'root' not found, using", output.file(DEFAULT_ROOT))
                    self.root = DEFAULT_ROOT
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
        with open(file, "w") as out:
            output.section("Saving changes...")
            try:
                json.dump({'root': self.root, 'data': self.entries}, out, indent=4)
                output.status("wrote to database", output.file(self.file))
            except:
                output.error("failed to write to database file")

    def _rename_entry(self, entry):
        _from = f"{entry['id']}.mp3"
        _to   = f"{file_name_from_title(entry['title'])}.mp3"
        _from = os.path.join(self.root, _from)
        _to   = os.path.join(self.root, _to)
        shutil.move(_from, _to)

    def entry_path(self, entry):
        return os.path.join(self.root, f"{file_name_from_title(entry['title'])}.mp3")

    def downloader(self, tracker: ProgressTracker):
        class MyLogger:
            def debug(self, msg):
                # For compatibility with youtube-dl, both debug and info are passed into debug
                # You can distinguish them by the prefix '[debug] '
                if not msg.startswith('[debug] '):
                    return self.info(msg)

                #output.status(msg)

            def info(self, msg: str):
                if msg.startswith('[download]'): return
                if msg.startswith('[ExtractAudio]'):
                    #tracker.print("Extracting audio...")
                    return
                if msg.startswith('[Metadata]'):
                    #tracker.print("Adding metadata...")
                    return
                if 'Downloading' in msg:
                    #tracker.print(msg[msg.index('Downloading'):])
                    return
                #print(msg)

            def warning(self, msg):
                pass#output.warning(msg)

            def error(self, msg):
                tracker.save_error(msg)

        actions = []
        if parse_title:
            actions.append((
                yt_dlp.postprocessor.metadataparser.MetadataParserPP.replacer,
                'year',
                '.*',
                '%(release_year)s',
            ))

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
            #    'pl_thumbnail': ''
            },
            'postprocessors': [
            #    {
            #        'key': 'MetadataParser',
            #        'actions': actions,
            #        'when': 'pre_process'
            #    },
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
            #    {
            #        'key': 'EmbedThumbnail',
            #        'already_have_thumbnail': False,
            #    },
                {
                    'key': 'FFmpegConcat',
                    'only_multi_video': True,
                    'when': 'playlist',
                }
            ],
            'retries': 10,
            #'writethumbnail': True
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
                tracker.progress.update(tracker.totalid, completed=p, total=100)
        elif d['status'] == 'finished':
            pass
        elif d['status'] == 'error':
            tracker.progress.stop_task(task_id)

    
# (debug help) python -m yt_dlp ID --no-download --write-info-json
def _info_to_entry(info: dict):
    new_entry = {'id': info['id']}
    if 'track' in info:
        new_entry['title']   = info['track']
        new_entry['artists'] = info['artists']
        new_entry['album']   = info['album']
        if info['release_year'] != None:
            new_entry['year'] = info['release_year']
    else:
        new_entry['artists'], new_entry['title'] = parse_title(info['title'])
    return new_entry
