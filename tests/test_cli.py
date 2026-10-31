import io, json, os, tempfile, time, unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from ytmm import cli, sources, status
from ytmm.library import Library

from test_sources import FakeYT

ATV = 'YQiau5iKLHc'  # Ava Max - Sleepwalker, in FakeYT
OMV = 'dQw4w9WgXcQ'  # Rick Astley - Never Gonna Give You Up


class CLITest(unittest.TestCase):
    role = 'client'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name).resolve()  # init resolves the root (/var is /private/var on macOS)
        self.root = d / 'root'
        self.config = d / 'config.toml'
        patch = mock.patch.dict(os.environ, {'YTMM_CONFIG': str(self.config)})
        patch.start()
        self.addCleanup(patch.stop)
        sources._yt = FakeYT()
        sources._album.cache_clear()
        self.addCleanup(setattr, sources, '_yt', None)
        code, _ = self.run_cli('init', str(self.root), '--name', 'laptop', '--role', self.role)
        self.assertEqual(code, 0)
        with open(self.config, 'a') as f:  # keep tests away from ~/.local/share/ytmm
            f.write(f'data = "{d / "data"}"\ndownloader = "fake"\nfake_delay = 0\n')

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def library(self) -> Library:
        return Library(self.root / 'library', 'test').load()


class TestInit(CLITest):
    def test_folders_and_config(self):
        self.assertTrue((self.root / 'library' / '.stignore').exists())
        self.assertIn('.*.part', (self.root / 'music' / '.stignore').read_text())
        self.assertTrue((self.root / 'music' / 'songs').is_dir())
        self.assertIn('device = "laptop"', self.config.read_text())
        code, out = self.run_cli('init', str(self.root))
        self.assertEqual(code, 1)
        self.assertIn('already exists', out)

    def test_config_command(self):
        code, out = self.run_cli('config')
        self.assertEqual(code, 0)
        self.assertIn('role = "client"', out)
        self.assertIn('downloader = "fake"', out)


class TestAdd(CLITest):
    def test_video_link(self):
        code, out = self.run_cli('add', f'https://music.youtube.com/watch?v={ATV}')
        self.assertEqual(code, 0, out)
        self.assertIn('added Ava Max - Sleepwalker (Diamonds & Dancefloors, 2023)', out)
        song = self.library().songs[ATV]
        self.assertTrue(song['cover'].endswith('=w720-h720-l90-rj'))
        code, out = self.run_cli('add', ATV)
        self.assertIn('already in the library', out)

    def test_overrides_and_tags(self):
        self.run_cli('add', OMV, '--title', 'NGGYU', '--year', '1987', '--tag', 'Gym')
        song = self.library().songs[OMV]
        self.assertEqual((song['title'], song['year'], song['tags']), ('NGGYU', 1987, ['gym']))
        code, out = self.run_cli('add', OMV, ATV, '--title', 'x')
        self.assertEqual(code, 1)

    def test_offline(self):
        self.run_cli('add', '--no-lookup', OMV)
        self.assertNotIn('title', self.library().songs[OMV])
        self.assertEqual(sources._yt.calls, [])

    def test_album(self):
        code, out = self.run_cli('add', 'https://music.youtube.com/playlist?list=OLAK5uy_mz6eafmqdRHSaR4IwG0ll6J6rgv0_ZpGw')
        self.assertEqual(code, 0, out)
        self.assertIn('Discovery (Daft Punk · 2001): 1 songs', out)
        self.assertEqual(self.library().songs['fa5IWHDbftI']['year'], 2001)

    def test_playlist_asks_first(self):
        with mock.patch.object(cli, 'interactive', return_value=True), \
             mock.patch.object(cli.Confirm, 'ask', return_value=False) as ask:
            code, _ = self.run_cli('add', 'https://music.youtube.com/playlist?list=PL5sCQfX0zpD2S4foifinyLwSljBhn_Sqz')
        self.assertEqual(code, 1)
        ask.assert_called_once()
        self.assertEqual(self.library().songs, {})
        self.run_cli('add', 'https://music.youtube.com/playlist?list=PL5sCQfX0zpD2S4foifinyLwSljBhn_Sqz', '-y')
        self.assertEqual(len(self.library().songs), 2)

    def test_search(self):
        code, out = self.run_cli('add', 'daft', 'punk', 'one', 'more', 'time')
        self.assertEqual(code, 1)
        self.assertIn('pass --first', out)
        code, out = self.run_cli('add', 'daft punk one more time', '--first')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.library().songs['wU26xVT_vBU']['year'], 2001)  # from its album

    def test_word_that_looks_like_a_video_id_is_searched(self):
        self.run_cli('add', 'beethoven99', '--first')
        self.assertIn(('search', 'beethoven99', 'songs'), sources._yt.calls)

    def test_clipboard(self):
        text = f'look: https://youtu.be/{ATV} and https://example.com'
        with mock.patch.object(sources, 'clipboard', return_value=text):
            code, out = self.run_cli('add')
        self.assertEqual(code, 0, out)
        self.assertIn('1 link from the clipboard', out)
        self.assertIn(ATV, self.library().songs)
        with mock.patch.object(sources, 'clipboard', return_value='nothing'):
            self.assertEqual(self.run_cli('add')[0], 1)


class TestSongs(CLITest):
    def setUp(self):
        super().setUp()
        self.run_cli('add', ATV, OMV)
        self.run_cli('add', '--no-lookup', 'aaaaaaaaaaa', '--title', 'Never Again', '--artist', 'Tyler, The Creator; Someone')

    def test_ls(self):
        code, out = self.run_cli('ls')
        self.assertEqual(code, 0)
        self.assertIn('Sleepwalker', out)
        self.assertIn('? ', out)  # no status.json from a server yet
        self.assertEqual(self.run_cli('ls', '--ids')[1].split(), [ATV, OMV, 'aaaaaaaaaaa'])
        self.assertEqual(self.run_cli('ls', 'rick', '--ids')[1].split(), [OMV])
        self.assertEqual(self.run_cli('ls', 'nevr', '--ids')[1].split(), [OMV, 'aaaaaaaaaaa'])  # typo
        self.assertEqual(self.run_cli('ls', '^never', '--regex', '--ids')[1].split(), [OMV, 'aaaaaaaaaaa'])
        self.assertEqual(self.run_cli('ls', '-s', 'title', '-l', '1', '--ids')[1].split(), [ATV])
        self.assertIn('3', self.run_cli('ls', '-n')[1])
        self.assertEqual(self.library().songs['aaaaaaaaaaa']['artists'], ['Tyler, The Creator', 'Someone'])

    def test_ls_marks_files(self):
        songs = self.root / 'music' / 'songs'
        (songs / f'Ava Max - Sleepwalker [{ATV}].mp3').write_text('')
        status.write(self.root / 'library' / 'status.json',
                     {'seen': time.time(), 'pending': [OMV], 'failed': {'aaaaaaaaaaa': {'error': 'x'}}})
        out = self.run_cli('ls')[1]
        self.assertRegex(out, r'✓\s+2023\s+Ava Max')
        self.assertRegex(out, r'…\s+--\s+Rick Astley')
        self.assertRegex(out, r'✗\s+--\s+Tyler')
        self.assertEqual(self.run_cli('ls', '-F')[1].strip(), str(songs / f'Ava Max - Sleepwalker [{ATV}].mp3'))
        self.assertEqual(self.run_cli('ls', '--no-downloaded', '--ids')[1].split(), [OMV, 'aaaaaaaaaaa'])
        self.assertEqual(self.run_cli('ls', '--failed', '--ids')[1].split(), ['aaaaaaaaaaa'])

    def test_edit(self):
        code, out = self.run_cli('edit', 'sleepwalker', '--album', 'Other', '--year', '')
        self.assertEqual(code, 0, out)
        song = self.library().songs[ATV]
        self.assertEqual(song['album'], 'Other')
        self.assertNotIn('year', song)
        self.assertIn('unchanged', self.run_cli('edit', ATV, '--album', 'Other')[1])

    def test_several_matches_need_a_picker(self):
        code, out = self.run_cli('edit', 'never', '--album', 'x')
        self.assertEqual(code, 1)
        self.assertIn('matches 2 songs', out)
        self.assertEqual(self.run_cli('edit', 'never', '--album', 'x', '--all')[0], 0)
        self.assertEqual(self.library().songs[OMV]['album'], 'x')

    def test_picker_is_used_when_interactive(self):
        with mock.patch.object(cli, 'interactive', return_value=True), \
             mock.patch('ytmm.picker.pick', return_value=['aaaaaaaaaaa']) as pick:
            code, _ = self.run_cli('edit', 'never', '--album', 'picked')
        self.assertEqual(code, 0)
        items, rank, query = pick.call_args[0][:3]
        self.assertEqual(query, 'never')
        self.assertEqual(len(items), 3)  # every song, pre-filtered by the query
        self.assertEqual(rank('never')[:2], [OMV, 'aaaaaaaaaaa'])
        self.assertEqual(self.library().songs['aaaaaaaaaaa']['album'], 'picked')

    def test_refresh(self):
        self.run_cli('edit', 'sleepwalker', '--title', 'wrong')
        self.run_cli('edit', ATV, '--refresh')
        self.assertEqual(self.library().songs[ATV]['title'], 'Sleepwalker')

    def test_rm_and_restore(self):
        code, out = self.run_cli('rm', 'rick', '-y')
        self.assertEqual(code, 0, out)
        self.assertTrue(self.library().songs[OMV]['deleted'])
        self.assertIn('Never Gonna Give You Up', self.run_cli('ls', '-d')[1])
        self.assertEqual(self.run_cli('restore', 'rick')[0], 0)
        self.assertNotIn('deleted', self.library().songs[OMV])
        self.assertEqual(self.run_cli('restore')[0], 0)  # nothing removed: says so
        self.assertEqual(self.run_cli('rm', 'zzzz', '-y')[0], 1)

    def test_tags(self):
        self.run_cli('tag', 'Gym', 'sleepwalker')
        self.run_cli('tag', 'gym', 'rick')
        self.assertEqual(self.library().songs[ATV]['tags'], ['gym'])
        out = self.run_cli('tags')[1]
        self.assertIn('#gym 2 songs', out)
        self.assertIn('playlists/gym.m3u', out)
        self.assertEqual(self.run_cli('ls', '-t', 'gym', '--ids')[1].split(), [ATV, OMV])
        self.run_cli('untag', 'gym', '--all')
        self.assertEqual(self.library().tags(), {})
        self.assertEqual(self.run_cli('untag', 'gym', '--all')[0], 1)


class TestStatus(CLITest):
    def test_no_server_yet(self):
        out = self.run_cli('status')[1]
        self.assertIn('no status.json yet', out)
        self.assertIn('this device (laptop, client): 0 songs, 0 files here', out)

    def test_report(self):
        self.run_cli('add', ATV)
        status.write(self.root / 'library' / 'status.json', {
            'server': 'homeserver', 'seen': time.time() - 120, 'ytdlp': '2026.10.26',
            'pending': ['x' * 11, 'y' * 11], 'downloading': 'z' * 11,
            'failed': {ATV: {'error': 'Video unavailable', 'attempts': 1, 'retry': time.time() + 3600}},
            'blocked': {'count': 12, 'limit': 10, 'token': 'abc123', 'reason': 'too many files to trash', 'files': ['f [x].mp3']},
            'canary': {'time': time.time(), 'ok': True, 'error': None},
            'inbox': {'time': time.time() - 60, 'ok': True, 'last_import': time.time() - 3600},
        })
        out = self.run_cli('status')[1]
        self.assertIn('server homeserver last seen 2 min ago · yt-dlp 2026.10.26 · canary OK', out)
        self.assertIn('3 downloading, 1 failed, 12 pending trash', out)
        self.assertIn(f'✗ Video unavailable: {ATV} (Ava Max - Sleepwalker; retry in', out)
        self.assertIn('ytmm approve', out)
        self.assertIn('inbox checked 1 min ago, last import 1 h ago', out)

        code, _ = self.run_cli('approve', '-y')
        self.assertEqual(code, 0)
        approval = json.loads((self.root / 'library' / 'approve.json').read_text())
        self.assertEqual((approval['token'], approval['by']), ('abc123', 'laptop'))

    def test_stale_server(self):
        status.write(self.root / 'library' / 'status.json', {'server': 's', 'seen': time.time() - 7200})
        self.assertIn('stopped reporting', self.run_cli('status')[1])


class TestServer(CLITest):
    role = 'server'

    def test_worker_once(self):
        self.run_cli('add', ATV, OMV, '--tag', 'gym')
        code, out = self.run_cli('worker', '--once')
        self.assertEqual(code, 0, out)
        files = sorted(p.name for p in (self.root / 'music' / 'songs').iterdir())
        self.assertEqual(files, [f'Ava Max - Sleepwalker [{ATV}].mp3', f'Rick Astley - Never Gonna Give You Up [{OMV}].mp3'])
        self.assertTrue((self.root / 'music' / 'playlists' / 'gym.m3u').exists())
        self.assertIn('0 downloading, 0 failed, 0 pending trash', self.run_cli('status')[1])

    def test_canary(self):
        code, out = self.run_cli('canary')
        self.assertEqual(code, 0, out)
        self.assertIn('canary OK', out)

    def test_migrate(self):
        old = Path(self.tmp.name) / 'old'
        (old / 'downloaded').mkdir(parents=True)
        db = {'root': 'downloaded/', 'playlists': {'new': [ATV]}, 'data': [
            {'id': ATV, 'title': 'Sleepwalker', 'artists': ['Ava Max'], 'album': 'Diamonds & Dancefloors', 'year': 2023},
            {'id': OMV, 'title': 'Never Gonna Give You Up', 'artists': ['Rick Astley']},
        ]}
        (old / 'downloaded' / 'music.json').write_text(json.dumps(db))
        (old / 'downloaded' / 'sleepwalker.mp3').write_text('audio')

        code, out = self.run_cli('migrate', str(old / 'downloaded' / 'music.json'), '--dry-run')
        self.assertEqual(code, 0, out)
        self.assertIn('1 files to move', out)
        self.assertIn('the server will download 1 songs', out)
        self.assertEqual(self.library().songs, {})

        code, out = self.run_cli('migrate', str(old / 'downloaded' / 'music.json'))
        self.assertEqual(code, 0, out)
        lib = self.library()
        self.assertEqual(lib.songs[ATV]['tags'], ['new'])
        self.assertLess(lib.songs[ATV]['added'], lib.songs[OMV]['added'])
        self.assertTrue((self.root / 'music' / 'songs' / f'Ava Max - Sleepwalker [{ATV}].mp3').exists())
        self.assertFalse((old / 'downloaded' / 'sleepwalker.mp3').exists())
        code, out = self.run_cli('migrate', str(old / 'downloaded' / 'music.json'), '-y')  # again: nothing new
        self.assertIn('0 of them new', out)


class TestClientLimits(CLITest):
    def test_worker_needs_server_role(self):
        code, out = self.run_cli('worker', '--once')
        self.assertEqual(code, 1)
        self.assertIn('runs on the server', out)

    def test_sync_needs_local(self):
        code, out = self.run_cli('sync')
        self.assertEqual(code, 1)
        self.assertIn('the server does', out)

    def test_sync_local(self):
        self.run_cli('add', ATV)
        code, out = self.run_cli('sync', '--local', '-y')
        self.assertEqual(code, 0, out)
        self.assertIn('Send & Receive', ' '.join(out.split()))
        self.assertEqual(len(list((self.root / 'music' / 'songs').iterdir())), 1)
        self.assertFalse((self.root / 'library' / 'status.json').exists())  # that's the server's


if __name__ == '__main__':
    unittest.main()
