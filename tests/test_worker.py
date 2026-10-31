import json, tempfile, time, unittest
from pathlib import Path
from ytmm import library, status
from ytmm.config import Config
from ytmm.download import DownloadError
from ytmm.library import Library
from ytmm.plan import tag_hash
from ytmm.sources import Track
from ytmm.worker import Worker, scan

A = 'aaaaaaaaaaa'
B = 'bbbbbbbbbbb'
C = 'ccccccccccc'


class Downloader:
    """Records what the worker asks for."""
    def __init__(self, info=None, fail=(), on_download=None):
        self.info = info or {}
        self.fail = set(fail)
        self.on_download = on_download
        self.downloads, self.tagged = [], []

    def version(self):
        return 'test'

    def download(self, id, song, tmp):
        self.downloads.append(id)
        if self.on_download:
            self.on_download(id)
        if id in self.fail:
            raise DownloadError('Video unavailable')
        path = tmp / f'{id}.mp3'
        path.write_text('audio')
        return path, self.info.get(id, {})

    def tag(self, path, id, song):
        self.tagged.append((id, song.get('title'), song.get('album')))
        path.write_text(f'audio tagged {song.get("album")}')


class WorkerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.cfg = Config(root=d / 'root', role='server', device='server', data=d / 'data',
                          downloader='fake', fake_delay=0)
        self.cfg.library_dir.mkdir(parents=True)
        self.notes = []

    def tearDown(self):
        self.tmp.cleanup()

    def worker(self, **kw):
        kw.setdefault('notify', lambda title, message: self.notes.append(title))
        return Worker(self.cfg, **kw)

    def client(self) -> Library:
        return Library(self.cfg.library_dir, 'laptop').load()

    def edit(self, fn):
        lib = self.client()
        fn(lib)
        lib.save()

    def status(self):
        return json.loads(self.cfg.status_file.read_text())

    def files(self):
        return sorted(scan(self.cfg.songs_dir))


class TestSync(WorkerTest):
    def test_end_to_end(self):
        w = self.worker()
        self.edit(lambda lib: (lib.put(A, title='Alpha', artists=['X']), lib.put(B, title='FAIL me')))
        w.run_once()
        self.assertEqual(self.files(), [f'X - Alpha [{A}].mp3'])
        st = self.status()
        self.assertEqual(st['failed'][B]['error'], 'simulated failure (title starts with FAIL)')
        self.assertEqual((st['songs'], st['files'], st['pending'], st['server']), (2, 1, [], 'server'))

        self.edit(lambda lib: (lib.put(A, title='Alpha 2'), lib.delete(B)))
        w.run_once()
        self.assertEqual(self.files(), [f'X - Alpha 2 [{A}].mp3'])
        self.assertEqual(self.status()['failed'], {})

        self.edit(lambda lib: lib.delete(A))
        w.run_once()
        self.assertEqual(self.files(), [])
        self.assertEqual(len(list(self.cfg.trash.iterdir())), 1)

    def test_conflict_copies_are_merged(self):
        library.write(self.cfg.library_dir / 'library.json', {A: {'title': 'a', 'artists': [], 'updated': 1}})
        library.write(self.cfg.library_dir / 'library.sync-conflict-20261031-120000-ABCDEFG.json',
                      {B: {'title': 'b', 'artists': [], 'updated': 1}})
        lines = self.worker().run_once()
        self.assertEqual(len(self.files()), 2)
        self.assertTrue(any(l.startswith('merged library.sync-conflict') for l in lines))

    def test_retag_after_edit(self):
        d = Downloader()
        w = self.worker(downloader=d)
        self.edit(lambda lib: lib.put(A, title='Alpha', album='One'))
        w.run_once()
        self.assertEqual(d.tagged, [(A, 'Alpha', 'One')])
        self.edit(lambda lib: lib.put(A, album='Two'))
        w.run_once()
        self.assertEqual(d.tagged[-1], (A, 'Alpha', 'Two'))
        self.assertEqual(self.files(), [f'Alpha [{A}].mp3'])  # same name: no rename
        self.assertEqual((self.cfg.songs_dir / f'Alpha [{A}].mp3').read_text(), 'audio tagged Two')
        self.assertEqual([p.name for p in self.cfg.songs_dir.iterdir()], [f'Alpha [{A}].mp3'])  # no .part left
        self.assertEqual(list(self.cfg.tmp_dir.iterdir()), [])
        self.assertEqual(w.state['tagged'][A], tag_hash(A, self.client().songs[A]))
        w.run_once()
        self.assertEqual(len(d.tagged), 2)

    def test_fills_in_metadata_of_songs_added_offline(self):
        info = {A: {'track': 'Alpha', 'artists': ['X'], 'album': 'Al', 'description': 'Released on: 2001-02-03'}}
        d = Downloader(info)
        w = self.worker(downloader=d)
        self.edit(lambda lib: (lib.put(B, title='Beta', added=1), lib.put(A, added=2)))
        w.run_once()
        song = self.client().songs[A]
        self.assertEqual((song['title'], song['artists'], song['album'], song['year']), ('Alpha', ['X'], 'Al', 2001))
        self.assertEqual(song['updated_by'], 'server')
        self.assertEqual(self.files(), [f'Beta [{B}].mp3', f'X - Alpha [{A}].mp3'])
        self.assertEqual(d.downloads, [A, B])  # writing the metadata didn't stop the pass

    def test_playlists_follow_tags(self):
        w = self.worker()
        self.edit(lambda lib: (lib.put(A, title='Alpha', tags=['gym']), lib.put(B, title='Beta', tags=['gym'])))
        w.run_once()
        text = (self.cfg.playlists_dir / 'gym.m3u').read_text()
        self.assertIn(f'../songs/Alpha [{A}].mp3', text)
        self.assertIn(f'../songs/Beta [{B}].mp3', text)
        self.edit(lambda lib: (lib.put(A, tags=[]), lib.put(B, tags=[])))
        w.run_once()
        self.assertEqual(list(self.cfg.playlists_dir.iterdir()), [])

    def test_library_change_stops_downloads(self):
        def add_song_during_first_download(id):
            if id == B:
                self.edit(lambda lib: lib.put(C, title='c', added=0))
        d = Downloader(on_download=add_song_during_first_download)
        w = self.worker(downloader=d)
        self.edit(lambda lib: (lib.put(A, title='a', added=1), lib.put(B, title='b', added=2)))
        lines = w.run_once()
        self.assertEqual(d.downloads, [B])
        self.assertIn('library changed, planning again', lines)
        w.run_once()
        self.assertEqual(d.downloads, [B, A, C])


class TestSafety(WorkerTest):
    def put_files(self, *ids):
        self.cfg.songs_dir.mkdir(parents=True, exist_ok=True)
        for id in ids:
            (self.cfg.songs_dir / f'x [{id}].mp3').write_text('')

    def test_trash_limit_and_approval(self):
        self.cfg.max_trash = 1
        self.put_files(A, B)
        library.write(self.cfg.library_dir / 'library.json', {})  # e.g. a bad merge emptied it
        w = self.worker()
        w.run_once()
        self.assertEqual(len(self.files()), 2)
        blocked = self.status()['blocked']
        self.assertEqual((blocked['count'], blocked['limit']), (2, 1))
        self.assertEqual(self.notes, ['ytmm: refused to delete songs'])
        w.run_once()
        self.assertEqual(len(self.notes), 1)  # not again for the same plan

        status.write(self.cfg.approve_file, {'token': 'wrong', 'by': 'laptop'})
        w.run_once()
        self.assertEqual(len(self.files()), 2)
        self.assertFalse(self.cfg.approve_file.exists())  # a stale approval is dropped

        status.write(self.cfg.approve_file, {'token': blocked['token'], 'by': 'laptop'})
        w.run_once()
        self.assertEqual(self.files(), [])
        self.assertFalse(self.cfg.approve_file.exists())
        self.assertIsNone(self.status()['blocked'])

    def test_limit_grows_with_the_library(self):
        self.cfg.max_trash, self.cfg.max_trash_percent = 1, 10
        live = {f'{i:011d}': {'title': str(i), 'artists': []} for i in range(30)}
        dead = {id: {'title': 'x', 'artists': [], 'deleted': True} for id in (A, B, C)}
        library.write(self.cfg.library_dir / 'library.json', {**live, **dead})
        self.put_files(A, B, C)
        self.worker().run_once()  # 3 of 33 songs: within 10%
        self.assertFalse(any(f.startswith('x [') for f in self.files()))

    def test_missing_library_never_trashes(self):
        self.put_files(A)
        self.worker().run_once()
        self.assertEqual(len(self.files()), 1)
        self.assertEqual(self.status()['blocked']['reason'], 'library.json is missing')

    def test_unreadable_library_does_nothing(self):
        self.put_files(A)
        (self.cfg.library_dir / 'library.json').write_text('{"version": 2, "songs": {')
        w = self.worker()
        w.run_once()
        w.run_once()
        self.assertEqual(len(self.files()), 1)
        self.assertIn('not valid JSON', self.status()['error'])
        self.assertEqual(self.notes, ['ytmm: library.json is unreadable'])

    def test_failed_downloads_back_off(self):
        d = Downloader(fail={A})
        w = self.worker(downloader=d)
        self.edit(lambda lib: lib.put(A, title='Alpha'))
        w.run_once()
        w.run_once()
        self.assertEqual(d.downloads, [A])
        failed = self.status()['failed'][A]
        self.assertEqual((failed['error'], failed['attempts']), ('Video unavailable', 1))
        self.assertGreater(failed['retry'], time.time())
        self.assertFalse(w._retry_due())

        w.state['failed'][A]['retry'] = 0  # time passes
        self.assertTrue(w._retry_due())
        w.run_once()
        self.assertEqual(w.state['failed'][A]['attempts'], 2)

        self.edit(lambda lib: lib.put(A, title='Alpha (fixed)'))  # an edit retries right away
        d.fail.clear()
        w.run_once()
        self.assertEqual(self.files(), [f'Alpha (fixed) [{A}].mp3'])
        self.assertEqual(w.state['failed'], {})

    def test_restore_takes_the_file_out_of_the_trash(self):
        d = Downloader()
        w = self.worker(downloader=d)
        self.edit(lambda lib: lib.put(A, title='Alpha'))
        w.run_once()
        self.edit(lambda lib: lib.delete(A))
        w.run_once()
        self.assertEqual(self.files(), [])
        self.edit(lambda lib: lib.put(A))
        lines = w.run_once()
        self.assertEqual(self.files(), [f'Alpha [{A}].mp3'])
        self.assertEqual(d.downloads, [A])
        self.assertIn(f'restored from trash Alpha [{A}].mp3', lines)
        self.assertEqual(list(self.cfg.trash.iterdir()), [])


class TestTimers(WorkerTest):
    def test_inbox(self):
        inbox = [Track(A, 'Alpha', ['X']), Track(B, 'Beta')]
        w = self.worker(fetch_inbox=lambda url: inbox)
        self.cfg.inbox = 'https://music.youtube.com/playlist?list=PLinbox0000'
        self.edit(lambda lib: (lib.put(B, title='Beta'), lib.delete(B), lib.put(C, title='Gamma')))

        self.assertEqual(w.check_inbox(), [A])  # B was deleted before the first check: stays deleted
        self.assertEqual(self.client().songs[A]['updated_by'], 'server')
        self.assertEqual(w.check_inbox(), [])

        self.edit(lambda lib: lib.delete(A))
        self.assertEqual(w.check_inbox(), [])  # still in the playlist, but deleted on a computer

        inbox[:] = [Track(B, 'Beta')]
        w.check_inbox()
        inbox[:] = [Track(B, 'Beta'), Track(A, 'Alpha', ['X'])]  # re-added to the playlist
        self.assertEqual(w.check_inbox(), [A])
        self.assertNotIn('deleted', self.client().songs[A])

    def test_inbox_errors_are_recorded(self):
        def broken(url):
            raise OSError('network down')
        w = self.worker(fetch_inbox=broken)
        self.assertEqual(w.check_inbox(), [])
        self.assertEqual(w.state.get('inbox')['error'], 'network down')

    def test_canary_notifies_when_it_changes(self):
        d = Downloader(fail={self.cfg.canary})
        w = self.worker(downloader=d)
        w.state['failed'][A] = {'error': 'x', 'attempts': 3, 'retry': time.time() + 1000, 'updated': 1}
        self.assertFalse(w.run_canary())
        self.assertFalse(w.run_canary())
        self.assertEqual(self.notes, ['ytmm: downloads are broken'])
        d.fail.clear()
        self.assertTrue(w.run_canary())
        self.assertEqual(self.notes[-1], 'ytmm: downloads work again')
        self.assertEqual(w.state['failed'][A]['retry'], 0)  # retried on the next pass
        self.assertTrue(w.state.get('canary')['ok'])

    def test_update_command(self):
        w = self.worker()
        self.cfg.update_command = 'echo upgraded'
        self.assertTrue(w.run_update())
        self.assertEqual(w.state.get('update')['output'], 'upgraded')
        self.cfg.update_command = 'echo nope >&2; exit 3'
        self.assertFalse(w.run_update())
        self.assertEqual(self.notes, ['ytmm: update failed'])

    def test_timers_run_when_due(self):
        calls = []
        w = self.worker(fetch_inbox=lambda url: calls.append('inbox') or [])
        w.run_canary = lambda: calls.append('canary')
        w.run_update = lambda: calls.append('update')
        self.cfg.inbox, self.cfg.update_command = 'PLinbox0000000', 'true'
        w._timers()
        self.assertEqual(calls, ['update', 'canary', 'inbox'])
        w._timers()
        self.assertEqual(calls, ['update', 'canary', 'inbox', 'update', 'canary'])  # inbox recorded its time

    def test_state_survives_restarts(self):
        w = self.worker()
        self.edit(lambda lib: lib.put(A, title='Alpha'))
        w.run_once()
        self.assertIn(A, self.worker().state['tagged'])


if __name__ == '__main__':
    unittest.main()
