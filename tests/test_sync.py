import json, tempfile, unittest
from pathlib import Path
from ytmm import library
from ytmm.config import Config
from ytmm.library import Library, merge
from ytmm.plan import plan, file_name
from ytmm.worker import Worker, scan

A = 'aaaaaaaaaaa'
B = 'bbbbbbbbbbb'


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
        dead = {A: {'deleted': True, 'updated': 2, 'updated_by': 'dev'}}
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


class TestPlan(unittest.TestCase):
    def test_actions(self):
        songs = {A: song('Alpha', 1), B: song('Beta', 1), 'ccccccccccc': {'deleted': True, 'updated': 1}}
        files = ['Old Name [aaaaaaaaaaa].mp3', 'Gone [ccccccccccc].mp3', 'random.txt']
        kinds = sorted((a.kind, a.id or '') for a in plan(songs, files))
        self.assertEqual(kinds, [('download', B), ('rename', A), ('trash', ''), ('trash', 'ccccccccccc')])

    def test_up_to_date(self):
        songs = {A: song('Alpha', 1)}
        self.assertEqual(plan(songs, [file_name(A, songs[A])]), [])


class TestWorker(unittest.TestCase):
    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = Config(root=d / 'root', role='server', device='server', trash=d / 'trash', fake_delay=0)
            cfg.library_dir.mkdir(parents=True)
            w = Worker(cfg)

            client = Library(cfg.library_dir, 'laptop').load()
            client.put(A, title='Alpha', artists=['X'])
            client.put(B, title='FAIL me')
            client.save()
            w.run_once()
            self.assertEqual(scan(cfg.songs_dir), ['X - Alpha [aaaaaaaaaaa].mp3'])
            st = json.loads(cfg.status_file.read_text())
            self.assertIn(B, st['failed'])

            client = Library(cfg.library_dir, 'laptop').load()
            client.put(A, title='Alpha 2')
            client.delete(B)
            client.save()
            w.run_once()
            self.assertEqual(scan(cfg.songs_dir), ['X - Alpha 2 [aaaaaaaaaaa].mp3'])

            client = Library(cfg.library_dir, 'laptop').load()
            client.delete(A)
            client.save()
            w.run_once()
            self.assertEqual(scan(cfg.songs_dir), [])
            self.assertEqual(len(list(cfg.trash.iterdir())), 1)

    def test_trash_limit(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cfg = Config(root=d / 'root', role='server', device='server', trash=d / 'trash', max_trash=1)
            cfg.songs_dir.mkdir(parents=True)
            cfg.library_dir.mkdir(parents=True)
            for id in (A, B):
                (cfg.songs_dir / f'x [{id}].mp3').write_text('')
            Worker(cfg).run_once()  # empty library would trash both
            self.assertEqual(len(scan(cfg.songs_dir)), 2)
            self.assertIsNotNone(json.loads(cfg.status_file.read_text())['blocked'])


if __name__ == '__main__':
    unittest.main()
