"""Offline server behavior with real ID3 tags and injected download failures."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ytmm import download, library, status
from ytmm.config import Config
from ytmm.library import Library
from ytmm.plan import file_name, metadata_key, plan
from ytmm.worker import Worker, worker_lock

A = 'aaaaaaaaaaa'
B = 'bbbbbbbbbbb'


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.cfg = Config(root=base / 'root', role='server', trash=base / 'trash', fake_delay=0)
        self.cfg.library_dir.mkdir(parents=True)
        self.lib = Library(self.cfg.library_dir, 'test').load()
        self.lib.put(A, title='Alpha', artists=['Artist'], album='First')
        self.lib.save()

    def test_album_only_change_retags_without_download(self):
        downloads, tags = [], []
        def fetch(id, song, dst):
            downloads.append(id)
            dst.write_bytes(b'audio')
        def tag(path, song):
            tags.append(song['album'])
        worker = Worker(self.cfg, downloader=fetch, tagger=tag)
        worker.run_once()
        self.lib.put(A, album='Second')
        self.lib.save()
        Worker(self.cfg, downloader=fetch, tagger=tag).run_once()
        self.assertEqual(downloads, [A])
        self.assertEqual(tags, ['Second'])
        self.assertFalse(status.read(self.cfg.status_file)['failed'])

    def test_missing_or_invalid_library_preserves_audio(self):
        self.cfg.songs_dir.mkdir(parents=True)
        audio = self.cfg.songs_dir / 'orphan.mp3'
        audio.write_bytes(b'audio')
        for content in (None, '{}', '{invalid'):
            path = self.cfg.library_dir / 'library.json'
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(content)
            Worker(self.cfg, fake=True).run_once()
            self.assertEqual(audio.read_bytes(), b'audio')
            self.assertTrue(status.read(self.cfg.status_file)['blocked'])

    def test_failed_download_retried_and_status_visible_during_fetch(self):
        attempts = []
        def fetch(id, song, dst):
            attempts.append(id)
            self.assertEqual(status.read(self.cfg.status_file)['downloading'], [A])
            if len(attempts) == 1:
                raise RuntimeError('network unavailable')
            dst.write_bytes(b'audio')
        worker = Worker(self.cfg, downloader=fetch)
        worker.run_once()
        self.assertEqual(status.read(self.cfg.status_file)['failed'], {A: 'network unavailable'})
        worker.run_once()
        self.assertEqual(attempts, [A, A])
        self.assertFalse(status.read(self.cfg.status_file)['failed'])

    def test_trash_guard_and_recovery(self):
        self.cfg.max_trash = 0
        Worker(self.cfg, fake=True).run_once()
        self.lib.delete(A)
        self.lib.save()
        Worker(self.cfg, fake=True).run_once()
        self.assertTrue(list(self.cfg.songs_dir.glob('*.mp3')))
        self.assertEqual(status.read(self.cfg.status_file)['pending_trash'], 1)
        self.cfg.max_trash = 1
        Worker(self.cfg, fake=True).run_once()
        self.assertEqual(list(self.cfg.songs_dir.glob('*.mp3')), [])
        self.assertEqual(len(list(self.cfg.trash.iterdir())), 1)

    def test_worker_lock_excludes_second_instance(self):
        with worker_lock(self.cfg):
            with self.assertRaisesRegex(RuntimeError, 'another worker'):
                Worker(self.cfg, fake=True).run_once()

    def test_restore_during_inbox_poll_preserves_audio(self):
        worker = Worker(self.cfg, fake=True)
        worker.run_once()
        audio = self.cfg.songs_dir / file_name(A, self.lib.songs[A])
        self.lib.delete(A)
        self.lib.save()

        def restore_during_poll(lib, log):
            restored = Library(self.cfg.library_dir, 'client').load()
            restored.put(A, title='Alpha', artists=['Artist'], album='First')
            restored.save()

        with patch.object(worker, '_inbox', side_effect=restore_during_poll):
            worker.run_once()
        self.assertTrue(audio.exists())
        self.assertFalse(library.read(self.lib.path)[A].get('deleted'))
        self.assertIn('postponing trash', status.read(self.cfg.status_file)['blocked'])

    def test_case_only_title_change_renames_file(self):
        worker = Worker(self.cfg, fake=True)
        worker.run_once()
        self.lib.put(A, title='ALPHA')
        self.lib.save()
        worker.run_once()
        self.assertEqual([path.name for path in self.cfg.songs_dir.iterdir()],
                         [file_name(A, self.lib.songs[A])])
        self.assertEqual(status.read(self.cfg.status_file)['failed'], {})

    def test_failed_rename_is_not_hidden_by_retag(self):
        worker = Worker(self.cfg, fake=True)
        worker.run_once()
        self.lib.put(A, title='Changed')
        self.lib.save()
        with patch('ytmm.worker.rename_song', side_effect=OSError('rename failed')):
            lines = worker.run_once()
        self.assertEqual(status.read(self.cfg.status_file)['failed'][A], 'rename failed')
        self.assertFalse(any(line.startswith('tagged ') for line in lines))

    def test_clients_need_explicit_local_flag(self):
        self.cfg.role = 'client'
        with self.assertRaises(ValueError):
            Worker(self.cfg)
        Worker(self.cfg, local=True, fake=True).run_once()
        self.assertTrue(status.read(self.cfg.status_file)['local'])

    def test_inbox_does_not_resurrect_tombstones(self):
        self.cfg.inbox_playlist = 'PLinbox'
        self.cfg.inbox_interval = 300
        self.lib.delete(B)
        self.lib.save()
        with patch('ytmm.sources.Source') as source:
            source.return_value.playlist.return_value = [(B, {'title': 'Deleted', 'artists': []}), ('ccccccccccc', {'title': 'New', 'artists': []})]
            Worker(self.cfg, fake=True).run_once()
        saved = library.read(self.cfg.library_dir / 'library.json')
        self.assertTrue(saved[B]['deleted'])
        self.assertEqual(saved['ccccccccccc']['title'], 'New')
        self.assertEqual(status.read(self.cfg.status_file)['inbox']['added'], 1)

    def test_tag_playlist_paths_and_removal(self):
        self.lib.put(A, tags=['gym', 'GYM', '../other'])
        self.lib.save()
        worker = Worker(self.cfg, fake=True)
        worker.run_once()
        playlists = list((self.cfg.music_dir / 'playlists').glob('*.m3u'))
        self.assertEqual(len(playlists), 3)
        for path in playlists:
            lines = path.read_text().splitlines()
            self.assertEqual(lines[0], '#EXTM3U')
            self.assertTrue((path.parent / lines[-1]).is_file())
        self.lib.put(A, tags=[])
        self.lib.save()
        worker.run_once()
        self.assertFalse(list((self.cfg.music_dir / 'playlists').glob('*.m3u')))

    def test_canary_status_and_notification_failure(self):
        self.cfg.canary_id = B
        self.cfg.canary_interval = 86400
        self.cfg.notify_url = 'https://notify.invalid/topic'
        def fetch(id, song, dst):
            if id == B:
                raise RuntimeError('canary broken')
            dst.write_bytes(b'audio')
        with patch('ytmm.worker.urlopen', side_effect=OSError('notify unavailable')):
            worker = Worker(self.cfg, downloader=fetch)
            worker.run_once()
        canary = status.read(self.cfg.status_file)['canary']
        self.assertFalse(canary['ok'])
        self.assertEqual(canary['error'], 'canary broken')
        self.assertEqual(canary['notification_error'], 'notify unavailable')
        self.assertFalse((self.cfg.state_dir / 'canary.mp3').exists())


class TagTests(unittest.TestCase):
    def test_real_id3_metadata_and_removal(self):
        try:
            from mutagen.id3 import ID3
        except ImportError:
            self.skipTest('server extra not installed')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'song.mp3'
            path.write_bytes(b'example audio payload')
            download.tag(path, {'title': 'Title', 'artists': ['A', 'B'], 'album': 'Album', 'year': 2026, 'track': 3})
            tags = ID3(path)
            self.assertEqual(str(tags['TIT2']), 'Title')
            self.assertEqual(str(tags['TPE1']), 'A/B')
            self.assertEqual(str(tags['TALB']), 'Album')
            self.assertEqual(str(tags['TRCK']), '3')
            self.assertEqual(str(tags['TDRC']), '2026')
            download.tag(path, {'title': 'Changed', 'artists': [], 'album': ''})
            tags = ID3(path)
            self.assertEqual(str(tags['TIT2']), 'Changed')
            self.assertNotIn('TALB', tags)
            self.assertNotIn('TDRC', tags)
            self.assertEqual(list(path.parent.glob('.ytmm-*')), [])

    def test_plan_retags_album_change_and_unicode_names_fit(self):
        song = {'title': '\u65e5' * 500, 'artists': [], 'album': 'New'}
        name = file_name(A, song)
        self.assertLess(len(name.encode()), 255)
        actions = plan({A: song}, [name], {A: metadata_key({**song, 'album': 'Old'})})
        self.assertEqual([a.kind for a in actions], ['retag'])

    def test_cover_art_embedded_and_failure_leaves_original(self):
        try:
            from mutagen.id3 import ID3
        except ImportError:
            self.skipTest('server extra not installed')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'song.mp3'
            path.write_bytes(b'audio')
            with patch('ytmm.download._cover', return_value=(b'jpeg bytes', 'image/jpeg')):
                download.tag(path, {'title': 'Title', 'cover_url': 'https://example.test/cover'})
            picture = ID3(path).getall('APIC')[0]
            self.assertEqual(picture.data, b'jpeg bytes')
            self.assertEqual(picture.mime, 'image/jpeg')
            old = path.read_bytes()
            with patch('ytmm.download._cover', side_effect=OSError('cover unavailable')):
                with self.assertRaises(OSError):
                    download.tag(path, {'title': 'Changed', 'cover_url': 'https://example.test/cover'})
            self.assertEqual(path.read_bytes(), old)

    def test_real_downloader_publishes_only_after_conversion_and_tags(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'published.mp3'
            session = MagicMock()
            session.__enter__.return_value = session
            def build(options):
                def extract(url, download):
                    self.assertFalse(destination.exists())
                    self.assertEqual(url, f'https://www.youtube.com/watch?v={A}')
                    Path(options['outtmpl'].replace('%(ext)s', 'mp3')).write_bytes(b'audio')
                    return {'id': A}
                session.extract_info.side_effect = extract
                return session
            module = SimpleNamespace(YoutubeDL=build)
            def tags(path, song):
                self.assertFalse(destination.exists())
                path.write_bytes(path.read_bytes() + b' tagged')
            with patch.dict('sys.modules', {'yt_dlp': module}), patch('ytmm.download._write_tags', side_effect=tags):
                download.download(A, {'title': 'Title'}, destination)
            self.assertEqual(destination.read_bytes(), b'audio tagged')
            self.assertEqual(list(destination.parent.glob('.ytmm-*')), [])

    def test_failed_tag_preserves_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'song.mp3'
            path.write_bytes(b'original')
            with patch('ytmm.download._write_tags', side_effect=OSError('write failed')):
                with self.assertRaises(OSError):
                    download.tag(path, {'title': 'New'})
            self.assertEqual(path.read_bytes(), b'original')
            self.assertEqual(list(path.parent.glob('.ytmm-*')), [])


if __name__ == '__main__':
    unittest.main()
