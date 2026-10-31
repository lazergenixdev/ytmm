import unittest
from ytmm.plan import file_id, file_name, playlist_name, playlists, plan, tag_hash

A = 'aaaaaaaaaaa'
B = 'bbbbbbbbbbb'
C = 'ccccccccccc'


def song(title, added=0, **kw):
    return {'title': title, 'artists': [], 'added': added, 'updated': 1, **kw}


class TestFileNames(unittest.TestCase):
    def test_name_and_id(self):
        name = file_name(A, {'title': 'Alpha?', 'artists': ['X', 'Y']})
        self.assertEqual(name, f'X, Y - Alpha [{A}].mp3')
        self.assertEqual(file_id(name), A)
        self.assertEqual(file_id('X - Alpha.mp3'), None)

    def test_long_names_fit_in_255_bytes(self):
        name = file_name(A, {'title': '붐바야' * 100, 'artists': ['BLACKPINK']}, '.m4a')
        self.assertLessEqual(len(name.encode('utf-8')) + len('.syncthing..tmp'), 255)
        self.assertEqual(file_id(name), A)
        name.encode('utf-8')  # no half characters

    def test_untitled(self):
        self.assertEqual(file_name(A, {}), f'{A} [{A}].mp3')
        self.assertEqual(file_name(A, {'title': '...'}), f'[{A}].mp3')


class TestPlan(unittest.TestCase):
    def test_actions(self):
        songs = {A: song('Alpha'), B: song('Beta'), C: song('Gone', deleted=True)}
        files = ['Old Name [aaaaaaaaaaa].mp3', 'Gone [ccccccccccc].mp3', 'random.txt']
        kinds = sorted((a.kind, a.id or '') for a in plan(songs, files))
        self.assertEqual(kinds, [('download', B), ('rename', A), ('trash', ''), ('trash', C)])

    def test_up_to_date(self):
        songs = {A: song('Alpha')}
        self.assertEqual(plan(songs, [file_name(A, songs[A])]), [])
        self.assertEqual(plan(songs, [file_name(A, songs[A])], {A: tag_hash(A, songs[A])}), [])

    def test_retag_when_tags_change(self):
        songs = {A: song('Alpha', album='One')}
        files = [file_name(A, songs[A])]
        tagged = {A: tag_hash(A, songs[A])}
        songs[A]['album'] = 'Two'
        self.assertEqual([(a.kind, a.src) for a in plan(songs, files, tagged)], [('retag', files[0])])
        self.assertEqual([a.kind for a in plan(songs, files, {})], ['retag'])  # never tagged (e.g. migrated)

    def test_tags_and_bookkeeping_dont_retag(self):
        songs = {A: song('Alpha')}
        tagged = {A: tag_hash(A, songs[A])}
        songs[A].update(tags=['gym'], updated=5, updated_by='desktop')
        self.assertEqual(plan(songs, [file_name(A, songs[A])], tagged), [])

    def test_rename_keeps_extension_and_retags_new_name(self):
        songs = {A: song('New')}
        actions = plan(songs, [f'Old [{A}].m4a'], {}, ext='.mp3')
        self.assertEqual([(a.kind, a.src, a.dst) for a in actions],
                         [('rename', f'Old [{A}].m4a', f'New [{A}].m4a'), ('retag', f'New [{A}].m4a', None)])

    def test_duplicates_keep_the_right_name(self):
        songs = {A: song('Alpha')}
        right = file_name(A, songs[A])
        actions = plan(songs, [f'Zzz [{A}].mp3', right])
        self.assertEqual([(a.kind, a.src, a.why) for a in actions], [('trash', f'Zzz [{A}].mp3', 'duplicate')])

    def test_downloads_newest_first(self):
        songs = {A: song('a', added=1), B: song('b', added=3), C: song('c', added=2)}
        self.assertEqual([a.id for a in plan(songs, [])], [B, C, A])

    def test_download_extension(self):
        songs = {A: song('a')}
        self.assertEqual(plan(songs, [], ext='.m4a')[0].dst, f'a [{A}].m4a')


class TestPlaylists(unittest.TestCase):
    def test_one_playlist_per_tag(self):
        songs = {
            A: song('Alpha', added=2, artists=['X'], tags=['gym', 'road trip']),
            B: song('Beta', added=1, tags=['gym']),
            C: song('Gamma', added=3, tags=['gym']),  # no file yet
        }
        names = {A: file_name(A, songs[A]), B: file_name(B, songs[B])}
        out = playlists(songs, names)
        self.assertEqual(sorted(out), ['gym.m3u', 'road_trip.m3u'])
        self.assertEqual(out['gym.m3u'].splitlines(), [
            '#EXTM3U', '#PLAYLIST:gym',
            '#EXTINF:-1,Beta', f'../songs/Beta [{B}].mp3',
            '#EXTINF:-1,X - Alpha', f'../songs/X - Alpha [{A}].mp3',
        ])

    def test_deleted_songs_are_left_out(self):
        songs = {A: song('Alpha', tags=['gym'], deleted=True)}
        self.assertEqual(playlists(songs, {A: 'x'}), {})

    def test_playlist_names_are_safe(self):
        self.assertEqual(playlist_name('._.'), '_.m3u')
        self.assertEqual(playlist_name('road trip'), 'road_trip.m3u')
        self.assertEqual(playlist_name('a/b'), 'ab.m3u')
        self.assertEqual(playlist_name('...'), '_.m3u')


if __name__ == '__main__':
    unittest.main()
