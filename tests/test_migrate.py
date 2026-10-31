import errno
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ytmm.config import Config
from ytmm.library import Library, read
from ytmm.migrate import migrate
from ytmm.plan import file_name

A = 'aaaaaaaaaaa'
B = 'bbbbbbbbbbb'


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.old = self.path / 'old'
        self.old.mkdir()
        self.source = self.path / 'music.json'
        self.cfg = Config(root=self.path / 'root', role='server', trash=self.path / 'trash')

    def legacy(self, entries, **more):
        data = {'root': str(self.old), 'data': entries, **more}
        self.source.write_text(json.dumps(data))
        return self.source.read_bytes()

    def test_import_moves_audio_preserves_metadata_and_is_repeatable(self):
        before = self.legacy([{'id': A, 'title': 'My Song', 'artists': ['Artist'], 'album': 'Album',
                               'year': 2020, 'path': '/old/machine/path'}], playlists={'gym': [A]})
        audio = self.old / 'my_song.mp3'
        audio.write_bytes(b'old music')
        report = migrate(self.cfg, self.source)
        self.assertEqual(report['added'], [A])
        self.assertEqual(len(report['moved']), 1)
        song = read(self.cfg.library_dir / 'library.json')[A]
        self.assertEqual(song['album'], 'Album')
        self.assertEqual(song['year'], 2020)
        self.assertEqual(song['tags'], ['gym'])
        self.assertNotIn('path', song)
        self.assertEqual((self.cfg.songs_dir / file_name(A, song)).read_bytes(), b'old music')
        self.assertFalse(audio.exists())
        self.assertEqual(self.source.read_bytes(), before)
        again = migrate(self.cfg, self.source)
        self.assertEqual(again['added'], [])
        self.assertEqual(again['moved'], [])

    def test_dry_run_does_not_create_destination_or_modify_source(self):
        before = self.legacy([{'id': A, 'title': 'Song', 'artists': []}])
        (self.old / 'song.mp3').write_bytes(b'audio')
        report = migrate(self.cfg, self.source, dry_run=True)
        self.assertEqual(report['added'], [A])
        self.assertEqual(len(report['moved']), 1)
        self.assertFalse(self.cfg.root.exists())
        self.assertEqual(self.source.read_bytes(), before)
        self.assertTrue((self.old / 'song.mp3').exists())

    def test_bad_legacy_entry_aborts_before_any_mutation(self):
        self.legacy([{'id': A, 'title': 'Good'}, {'id': 'bad', 'title': 'Bad'}])
        with self.assertRaises(ValueError):
            migrate(self.cfg, self.source)
        self.assertFalse(self.cfg.root.exists())

    def test_existing_tombstones_are_not_resurrected(self):
        self.legacy([{'id': A, 'title': 'Song'}])
        audio = self.old / 'song.mp3'
        audio.write_bytes(b'audio')
        lib = Library(self.cfg.library_dir, 'server').load()
        lib.delete(A)
        lib.save()
        report = migrate(self.cfg, self.source)
        self.assertEqual(report['added'], [])
        self.assertTrue(read(lib.path)[A]['deleted'])
        self.assertTrue(audio.exists())

    def test_ambiguous_old_filenames_preserved_and_reported(self):
        self.legacy([{'id': A, 'title': 'Same'}, {'id': B, 'title': 'Same'}])
        audio = self.old / 'same.mp3'
        audio.write_bytes(b'audio')
        report = migrate(self.cfg, self.source)
        self.assertEqual(report['missing'], [A, B])
        self.assertEqual(report['moved'], [])
        self.assertTrue(audio.exists())

    def test_destination_is_never_overwritten(self):
        self.legacy([{'id': A, 'title': 'Song'}])
        old = self.old / 'song.mp3'
        old.write_bytes(b'old')
        self.cfg.songs_dir.mkdir(parents=True)
        destination = self.cfg.songs_dir / file_name(A, {'title': 'Song'})
        destination.write_bytes(b'existing')
        report = migrate(self.cfg, self.source)
        self.assertEqual(report['moved'], [])
        self.assertEqual(destination.read_bytes(), b'existing')
        self.assertEqual(old.read_bytes(), b'old')

    def test_relative_legacy_root_inside_output_directory(self):
        self.source = self.old / 'music.json'
        self.source.write_text(json.dumps({'root': 'old/', 'data': [{'id': A, 'title': 'Song'}]}))
        (self.old / 'song.mp3').write_bytes(b'audio')
        report = migrate(self.cfg, self.source)
        self.assertEqual(len(report['moved']), 1)
        self.assertEqual(report['missing'], [])

    def test_id_named_interrupted_download_is_imported(self):
        self.legacy([{'id': A, 'title': 'Song'}])
        (self.old / f'{A}.mp3').write_bytes(b'audio')
        report = migrate(self.cfg, self.source)
        self.assertEqual(len(report['moved']), 1)
        self.assertEqual(report['missing'], [])

    def test_interrupted_move_keeps_database_and_can_resume(self):
        self.legacy([{'id': A, 'title': 'Song'}])
        old = self.old / 'song.mp3'
        old.write_bytes(b'audio')
        with patch('ytmm.migrate._move_exclusive', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                migrate(self.cfg, self.source)
        self.assertIn(A, read(self.cfg.library_dir / 'library.json'))
        self.assertTrue(old.exists())
        report = migrate(self.cfg, self.source)
        self.assertEqual(report['added'], [])
        self.assertEqual(len(report['moved']), 1)

    def test_cross_filesystem_copy_is_hidden_until_complete(self):
        self.legacy([{'id': A, 'title': 'Song'}])
        old = self.old / 'song.mp3'
        old.write_bytes(b'complete audio')
        destination = self.cfg.songs_dir / file_name(A, {'title': 'Song'})
        real_link, real_copy = os.link, shutil.copyfileobj
        def link(source, target):
            if Path(source).resolve() == old.resolve():
                raise OSError(errno.EXDEV, 'cross-device link')
            return real_link(source, target)
        def copy(source, target):
            self.assertFalse(destination.exists())
            return real_copy(source, target)
        with patch('ytmm.migrate.os.link', side_effect=link), patch('ytmm.migrate.shutil.copyfileobj', side_effect=copy):
            report = migrate(self.cfg, self.source)
        self.assertEqual(len(report['moved']), 1)
        self.assertEqual(destination.read_bytes(), b'complete audio')
        self.assertFalse(old.exists())
        self.assertEqual(list(destination.parent.glob('.ytmm-migrate-*')), [])

    def test_interrupted_cross_filesystem_copy_never_publishes_partial_audio(self):
        self.legacy([{'id': A, 'title': 'Song'}])
        old = self.old / 'song.mp3'
        old.write_bytes(b'complete audio')
        destination = self.cfg.songs_dir / file_name(A, {'title': 'Song'})
        def copy(source, target):
            target.write(b'partial')
            target.flush()
            self.assertFalse(destination.exists())
            raise OSError('interrupted copy')
        with patch('ytmm.migrate.os.link', side_effect=OSError(errno.EXDEV, 'cross-device link')), patch('ytmm.migrate.shutil.copyfileobj', side_effect=copy):
            with self.assertRaisesRegex(OSError, 'interrupted copy'):
                migrate(self.cfg, self.source)
        self.assertFalse(destination.exists())
        self.assertEqual(old.read_bytes(), b'complete audio')
        self.assertEqual(list(destination.parent.glob('.ytmm-migrate-*')), [])
        self.assertEqual(len(migrate(self.cfg, self.source)['moved']), 1)

    def test_cross_filesystem_publish_cannot_replace_concurrent_destination(self):
        self.legacy([{'id': A, 'title': 'Song'}])
        old = self.old / 'song.mp3'
        old.write_bytes(b'original audio')
        destination = self.cfg.songs_dir / file_name(A, {'title': 'Song'})
        real_link, real_copy = os.link, shutil.copyfileobj
        def link(source, target):
            if Path(source).resolve() == old.resolve():
                raise OSError(errno.EXDEV, 'cross-device link')
            return real_link(source, target)
        def copy(source, target):
            real_copy(source, target)
            destination.write_bytes(b'concurrent audio')
        with patch('ytmm.migrate.os.link', side_effect=link), patch('ytmm.migrate.shutil.copyfileobj', side_effect=copy):
            with self.assertRaises(FileExistsError):
                migrate(self.cfg, self.source)
        self.assertEqual(destination.read_bytes(), b'concurrent audio')
        self.assertEqual(old.read_bytes(), b'original audio')
        self.assertEqual(list(destination.parent.glob('.ytmm-migrate-*')), [])

    def test_client_cannot_migrate(self):
        self.legacy([])
        self.cfg.role = 'client'
        with self.assertRaises(ValueError):
            migrate(self.cfg, self.source)
