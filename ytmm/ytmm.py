import logging
import json
import os
import shutil
import concurrent.futures
import yt_dlp
#import ffmpeg
from . import output
from .utils import (
    file_name_from_title,
    parse_title,
    progress_bar
)

DEFAULT_ROOT = "music"

"""
Entry:
    'id':      str,
    'title':   str,
    'artists': list[str], [optional]
    'album':   str,       [optional]
    'year':    int,       [optional]
    'path':    str        [optional] (defaults to root)
"""

class ProgressTracker:
    def __init__(self, urls):
        self.urls = urls
        self.tracked = dict()
        self.errors = []
    
    def index(self, _id):
        for i, url in enumerate(self.urls):
            if _id in url:
                return i
        return -1
    
    def save_error(self, error):
        self.errors.append(error)


class YoutubeMM:
    def __init__(self, database_file="db.json"):
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        self.file = database_file
        self.modified = False
        #self.logger.info("database file: %s", database_file)

    def __enter__(self):
        self.load()
        return self
    
    def __exit__(self, *args):
        if self.modified:
            self.save_to(self.file)


    def sync(self, output_dir):
        if output_dir:
            self.root = output_dir
        output.section(f'Syncronizing music files...')

        # Here we need to download everything
        if not os.path.exists(self.root):
            output.status(f'root {output.file(self.root)} not found')
            
            if not output.ask(f"Create new root directory?"):
                return
            
            output.status("creating directory...")
            os.mkdir(self.root)

            self.download(self.entries)
            return

        entries = []
        # Only download what does not exist
        for entry in self.entries:
            file_name = f"{file_name_from_title(entry['title'])}.mp3"
            path = os.path.join(self.root, file_name)
            if not os.path.exists(path):
                output.status(file_name, "not found")
                entries.append(entry)

        if not entries:
            output.status("nothing to do")
            return

        print()
        output.section("Music to download:")
        for entry in entries:
            output.status(file_name_from_title(entry['title']), end=' ')
        print('\n')

        if not output.ask("Proceed to download?"): return

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

        def download(url: str, index: int):
            info = d.extract_info(url, download=True)
            new_entry = _info_to_entry(info)
            self._rename_entry(new_entry)
        
            if index >= 0:
                self.entries[index] = new_entry
            else:
                self.entries.append(new_entry)
            self.modified = True
            
        tracker = ProgressTracker([url for url,_ in download_list])
        with self.downloader(tracker) as d:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                for url, index in download_list:
                    executor.submit(download, url, index)
                executor.shutdown()
                output.concurrent(len(urls), '') # reset cursor
        
        for error in tracker.errors:
            output.status(error)



    def remove(self, _):
        output.status("not implemented :(")
        pass


    def download(self, entries):
        output.section("Retrieving music...")

        urls = [entry['id'] for entry in entries]
        
        tracker = ProgressTracker(urls)

        def download(url):
            return d.download(url)

        with self.downloader(tracker) as d:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                #[download(url) for url in urls]
                for url in urls:
                    executor.submit(download, url)
                executor.shutdown()
                output.concurrent(len(urls), '') # reset cursor

        for entry in entries:
            self._rename_entry(entry)
        

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

        #actions = []
        #if parse_title:
        #    actions.append((
        #        yt_dlp.postprocessor.metadataparser.MetadataParserPP.interpretter,
        #        'title', '%(artist)s - %(title)s'
        #    ))

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
        index = tracker.index(d['info_dict']['id'])
        if d['status'] == 'downloading':
            entry = _info_to_entry(d['info_dict'])
            progress = d['downloaded_bytes']/d['total_bytes']
            w,_ = os.get_terminal_size()
            output.concurrent(index, f"{1+index:3} {file_name_from_title(entry['title']):36} {progress_bar(progress, w=(w-42))}")
        if d['status'] == 'finished':
            entry = _info_to_entry(d['info_dict'])
            w,_ = os.get_terminal_size()
            output.concurrent(index, f"{1+index:3} {file_name_from_title(entry['title']):36} {progress_bar(1.0, w=(w-42))}")
        elif d['status'] == 'error':
            output.concurrent(index, f"{d['status']}")

    
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
