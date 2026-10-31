import json, tempfile, unittest
from pathlib import Path
from ytmm import library
from ytmm.library import Library, LibraryError, merge, parse_id, search

A = 'aaaaaaaaaaa'
B = 'bbbbbbbbbbb'
C = 'ccccccccccc'


def song(title, updated, by='dev', **kw):
    return {'title': title, 'artists': [], 'updated': updated, 'updated_by': by, **kw}


class TestMerge(unittest.TestCase):
    def test_newest_wins_per_song(self):
        x = {A: song('old', 1), B: song('new b', 5)}
        y = {A: song('new', 2), B: song('old b', 3)}
        m = merge(x, y)
        self.assertEqual(m[A]['title'], 'new')
        self.assertEqual(m[B]['title'], 'new b')

    def test_tombstone_not_resurrected(self):
        live = {A: song('a', 1)}
        dead = {A: song('a', 2, deleted=True)}
        self.assertTrue(merge(live, dead)[A]['deleted'])
        self.assertTrue(merge(dead, live)[A]['deleted'])

    def test_deterministic(self):
        x = {A: song('x', 1, 'laptop')}
        y = {A: song('y', 1, 'desktop')}
        self.assertEqual(merge(x, y), merge(y, x))

    def test_load_merges_conflict_copies(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            library.write(d / 'library.json', {A: song('a', 1)})
            library.write(d / 'library.sync-conflict-20261031-120000-ABCDEFG.json', {B: song('b', 1)})
            lib = Library(d, 'dev').load()
            self.assertEqual(set(lib.songs), {A, B})
            self.assertEqual(list(d.glob('library.sync-conflict-*')), [])
            self.assertEqual(set(library.read(d / 'library.json')), {A, B})

    def test_unreadable_conflict_copy_is_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            library.write(d / 'library.json', {A: song('a', 1)})
            bad = d / 'library.sync-conflict-20261031-120000-ABCDEFG.json'
            bad.write_text('{"data": []}')  # v0.2 format
            lib = Library(d, 'dev').load()
            self.assertEqual(set(lib.songs), {A})
            self.assertTrue(bad.exists())

    def test_unreadable_library_raises(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'library.json').write_text('{"version": 2, "songs": {')
            with self.assertRaises(LibraryError):
                Library(d, 'dev').load()

    def test_missing_folder_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(LibraryError):
                Library(Path(d) / 'nope', 'dev').load()


class TestEdits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lib = Library(Path(self.tmp.name), 'laptop').load()

    def tearDown(self):
        self.tmp.cleanup()

    def test_put_sets_bookkeeping(self):
        self.assertTrue(self.lib.put(A, title='Alpha', artists=['X']))
        s = self.lib.songs[A]
        self.assertEqual(s['updated_by'], 'laptop')
        self.assertEqual(s['added'], s['updated'])

    def test_put_without_changes_is_not_an_edit(self):
        self.lib.put(A, title='Alpha', artists=['X'])
        before = dict(self.lib.songs[A])
        self.assertFalse(self.lib.put(A, title='Alpha'))
        self.assertEqual(self.lib.songs[A], before)

    def test_put_removes_optional_fields_with_empty_values(self):
        self.lib.put(A, title='Alpha', album='Album', year=2001, tags=['gym'])
        self.lib.put(A, album='', year='', tags=[])
        self.assertEqual({'album', 'year', 'tags'} & set(self.lib.songs[A]), set())
        self.assertEqual(self.lib.songs[A]['title'], 'Alpha')

    def test_tags_are_normalized(self):
        self.lib.put(A, title='Alpha', tags=['Gym', ' gym ', 'Road  Trip', ''])
        self.assertEqual(self.lib.songs[A]['tags'], ['gym', 'road trip'])

    def test_delete_keeps_metadata_and_restore_revives(self):
        self.lib.put(A, title='Alpha', artists=['X'], added=5)
        self.assertTrue(self.lib.delete(A))
        self.assertFalse(self.lib.delete(A))
        self.assertTrue(self.lib.songs[A]['deleted'])
        self.assertEqual(self.lib.songs[A]['title'], 'Alpha')
        self.assertEqual(self.lib.live(), {})
        self.assertTrue(self.lib.put(A))  # restore
        self.assertNotIn('deleted', self.lib.songs[A])
        self.assertEqual(self.lib.songs[A]['added'], 5)

    def test_tags_counts_live_songs(self):
        self.lib.put(A, title='a', tags=['gym'])
        self.lib.put(B, title='b', tags=['gym', 'chill'])
        self.lib.put(C, title='c', tags=['gym'])
        self.lib.delete(C)
        self.assertEqual(self.lib.tags(), {'chill': 1, 'gym': 2})

    def test_save_is_sorted_and_stable(self):
        self.lib.put(B, title='b')
        self.lib.put(A, title='a')
        self.lib.save()
        text = self.lib.path.read_text()
        self.assertLess(text.index(A), text.index(B))
        self.assertEqual(json.loads(text)['version'], 2)


class TestParseId(unittest.TestCase):
    def test_urls(self):
        for url in ['dQw4w9WgXcQ', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
                    'https://music.youtube.com/watch?v=dQw4w9WgXcQ&list=RDAMVMdQw4w9WgXcQ',
                    'https://youtu.be/dQw4w9WgXcQ?si=abc', 'https://www.youtube.com/shorts/dQw4w9WgXcQ']:
            self.assertEqual(parse_id(url), 'dQw4w9WgXcQ', url)
        self.assertIsNone(parse_id('https://music.youtube.com/playlist?list=PLabcdefghijklmnop'))
        self.assertIsNone(parse_id('daft punk'))


class TestSearch(unittest.TestCase):
    songs = {
        'id00000000a': {'title': 'One More Time', 'artists': ['Daft Punk'], 'album': 'Discovery'},
        'id00000000b': {'title': 'Around the World', 'artists': ['Daft Punk'], 'album': 'Homework'},
        'id00000000c': {'title': 'Halo', 'artists': ['Beyoncé'], 'album': 'I Am... Sasha Fierce'},
        'id00000000d': {'title': 'Dance Floor', 'artists': ['Aft Pink']},
        'id00000000e': {'title': 'Femininomenon', 'artists': ['Chappell Roan']},
    }

    def test_words_match_title_artist_and_album_together(self):
        self.assertEqual(search(self.songs, 'daft discovery'), ['id00000000a'])
        self.assertEqual(set(search(self.songs, 'DAFT')), {'id00000000a', 'id00000000b'})

    def test_accents_are_ignored(self):
        self.assertEqual(search(self.songs, 'beyonce halo'), ['id00000000c'])

    def test_typos(self):
        self.assertEqual(search(self.songs, 'dafy punk'), ['id00000000a', 'id00000000b'])
        self.assertEqual(search(self.songs, 'chapel roan'), ['id00000000e'])

    def test_substrings_beat_typos(self):
        # "aft pink" is in one song; "daft punk" is a typo away from it but shouldn't show up in `ls`
        self.assertEqual(search(self.songs, 'aft pink'), ['id00000000d'])
        both = search(self.songs, 'aft pink', best=False)
        self.assertEqual(both[0], 'id00000000d')
        self.assertIn('id00000000a', both)

    def test_scattered_letters_last(self):
        self.assertEqual(search(self.songs, 'fmnnmn'), ['id00000000e'])

    def test_no_match(self):
        self.assertEqual(search(self.songs, 'zzzz'), [])

    def test_empty_query_returns_everything(self):
        self.assertEqual(search(self.songs, '  '), list(self.songs))

    def test_regex(self):
        self.assertEqual(search(self.songs, '^around', regex=True), ['id00000000b'])
        self.assertEqual(search(self.songs, '^around', regex=True, case=True), [])
        self.assertEqual(search(self.songs, 'Homework$', regex=True), ['id00000000b'])


if __name__ == '__main__':
    unittest.main()
