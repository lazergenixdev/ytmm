import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ytmm.library import Library, merge, parse_id, read, write

A = 'aaaaaaaaaaa'
B = 'bbbbbbbbbbb'


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_video_urls_are_parsed_only_from_youtube(self):
        for value in (A, f'https://youtu.be/{A}?t=5', f'https://music.youtube.com/watch?v={A}&list=ABC',
                      f'https://www.youtube.com/shorts/{A}'):
            self.assertEqual(parse_id(value), A)
        for value in (f'https://example.org/watch?v={A}', A + 'b', f'https://youtu.be/{A}b', None):
            self.assertIsNone(parse_id(value))

    def test_two_local_writers_preserve_distinct_edits(self):
        first = Library(self.root, 'one').load()
        second = Library(self.root, 'two').load()
        first.put(A, title='First', artists=['One'])
        second.put(B, title='Second', artists=['Two'])
        first.save()
        second.save()
        self.assertEqual(set(read(first.path)), {A, B})

    def test_stale_writer_does_not_resurrect_tombstone(self):
        lib = Library(self.root, 'one').load()
        lib.put(A, title='First')
        lib.save()
        stale = Library(self.root, 'two').load()
        lib.delete(A)
        lib.save()
        stale.put(B, title='Second')
        stale.save()
        self.assertTrue(read(lib.path)[A]['deleted'])

    def test_clock_backwards_still_allows_edit(self):
        lib = Library(self.root, 'one')
        with patch('ytmm.library.time.time', return_value=200):
            lib.put(A, title='First')
        with patch('ytmm.library.time.time', return_value=100):
            lib.put(A, title='Second')
        self.assertGreater(lib.songs[A]['updated'], 200)

    def test_corrupt_main_file_is_never_replaced(self):
        path = self.root / 'library.json'
        path.write_text('{"version":2,"songs":[]}')
        with self.assertRaises(ValueError):
            Library(self.root, 'one').load()
        self.assertEqual(json.loads(path.read_text())['songs'], [])

    def test_bad_conflict_is_retained_and_good_conflict_merged(self):
        lib = Library(self.root, 'one')
        lib.put(A, title='First')
        lib.save()
        bad = self.root / 'library.sync-conflict-bad.json'
        bad.write_text('{')
        good = self.root / 'library.sync-conflict-good.json'
        write(good, {B: {'title': 'Other', 'artists': [], 'updated': 1}})
        with self.assertLogs('ytmm.library', level='WARNING'):
            lib.load()
        self.assertEqual(set(lib.songs), {A, B})
        self.assertTrue(bad.exists())
        self.assertFalse(good.exists())
        with self.assertLogs('ytmm.library', level='WARNING'):
            lib.load()
        self.assertEqual(lib.merged_conflicts, [])

    def test_invalid_metadata_is_rejected_before_write(self):
        path = self.root / 'library.json'
        for entry in ({'title': 'Bad', 'artists': [], 'updated': float('nan')},
                      {'title': 'Bad', 'artists': 'one', 'updated': 1},
                      {'deleted': 'yes', 'updated': 1}):
            with self.assertRaises(ValueError):
                write(path, {A: entry})
        self.assertFalse(path.exists())

    def test_merge_tie_is_deterministic_and_does_not_alias_input(self):
        one = {A: {'title': 'One', 'artists': [], 'updated': 1, 'updated_by': 'same'}}
        two = {A: {'title': 'Two', 'artists': [], 'updated': 1, 'updated_by': 'same'}}
        self.assertEqual(merge(one, two), merge(two, one))
        merged = merge(one)
        merged[A]['artists'].append('New')
        self.assertEqual(one[A]['artists'], [])

    def test_failed_replace_preserves_old_file_and_cleans_temp(self):
        lib = Library(self.root, 'one')
        lib.put(A, title='One')
        lib.save()
        before = lib.path.read_bytes()
        lib.put(B, title='Two')
        with patch('ytmm.library.os.replace', side_effect=OSError('simulated')):
            with self.assertRaises(OSError):
                lib.save()
        self.assertEqual(lib.path.read_bytes(), before)
        self.assertEqual(list(self.root.glob('.library.json.*.tmp')), [self.root / '.library.json.lock.tmp'])
