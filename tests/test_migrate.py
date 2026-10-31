import tempfile, unittest
from pathlib import Path
from ytmm.migrate import convert, files_dir, match_files, v0_file_name

A = 'aaaaaaaaaaa'
B = 'bbbbbbbbbbb'
C = 'ccccccccccc'
D = 'ddddddddddd'

DB = {
    'root': 'downloaded/',
    'data': [
        {'id': A, 'title': 'One Of Us', 'artists': ['Ava Max'], 'album': 'Diamonds & Dancefloors', 'year': 2023, 'path': '1'},
        {'id': B, 'title': 'Halo', 'artists': ['Beyoncé']},
        {'id': C, 'title': 'Halo', 'artists': ['Someone Else']},  # same v0.2 file name as B
        {'id': A, 'title': 'duplicate entry'},
        {'id': 'not an id', 'title': 'broken'},
        {'title': 'no id'},
    ],
    'playlists': {'new': [B, A], '._.': [B], 'A': [C]},
}


class TestConvert(unittest.TestCase):
    def test_entries(self):
        songs = convert(DB, now=1000.0)
        self.assertEqual(list(songs), [A, B, C])
        self.assertEqual(songs[A], {'title': 'One Of Us', 'artists': ['Ava Max'], 'album': 'Diamonds & Dancefloors',
                                    'year': 2023, 'tags': ['new'], 'added': 1000.0 - 4})  # 4 valid entries
        self.assertEqual(songs[B]['tags'], ['._.', 'new'])
        self.assertEqual(songs[C]['tags'], ['a'])
        self.assertLess(songs[A]['added'], songs[B]['added'])  # v0.2 order is kept

    def test_v0_file_name(self):
        self.assertEqual(v0_file_name('One Of Us'), 'one_of_us')
        self.assertEqual(v0_file_name('Song (feat. X) (Official Video)'), 'song')
        self.assertEqual(v0_file_name('붐바야'), '')


class TestFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_files_dir_is_relative_to_where_v0_ran(self):
        (self.dir / 'downloaded').mkdir()
        music_json = self.dir / 'downloaded' / 'music.json'
        self.assertEqual(files_dir(music_json, DB), (self.dir / 'downloaded').resolve())
        self.assertIsNone(files_dir(music_json, {'root': 'nowhere'}))

    def test_match_files(self):
        songs = convert(DB, now=1000.0)
        songs[D] = {'title': 'Fourth', 'artists': []}
        paths = [self.dir / n for n in [
            'one_of_us.mp3',            # v0.2 name
            'halo.mp3',                 # v0.2 name shared by B and C: needs the embedded tags
            f'{B}.mp3',                 # downloaded, never renamed
            f'{D}.mp3',                 # second file for D...
            f'Fourth [{D}].mp3',        # ...which already has an id-based name, so it wins
            'unknown_song.mp3',
        ]]
        tagged = {'halo.mp3': C}
        found, unmatched = match_files(songs, paths, read_id=lambda p: tagged.get(p.name))
        self.assertEqual({p.name: id for p, id in found.items()},
                         {'one_of_us.mp3': A, 'halo.mp3': C, f'{B}.mp3': B, f'Fourth [{D}].mp3': D})
        self.assertEqual(sorted(p.name for p in unmatched), [f'{D}.mp3', 'unknown_song.mp3'])

    def test_ambiguous_names_without_tags_are_left_alone(self):
        found, unmatched = match_files(convert(DB, now=0), [self.dir / 'halo.mp3'])
        self.assertEqual((found, [p.name for p in unmatched]), ({}, ['halo.mp3']))


if __name__ == '__main__':
    unittest.main()
