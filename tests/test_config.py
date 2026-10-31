import tempfile
import unittest
from pathlib import Path

from ytmm.config import Config, load, save


class ConfigTests(unittest.TestCase):
    def test_round_trip_optional_settings_and_escaping(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            cfg = Config(root=path / 'root', trash=path / 'trash', device='a"b\\c\nname',
                         inbox_playlist='https://music.youtube.com/playlist?list=PLabc',
                         canary_id='https://youtu.be/aaaaaaaaaaa', notify_url='https://ntfy.example/topic')
            save(cfg, path / 'config.toml')
            self.assertEqual(load(path / 'config.toml'), cfg)
            self.assertEqual(cfg.canary_id, 'aaaaaaaaaaa')
            self.assertFalse(cfg.state_dir.is_relative_to(cfg.music_dir))

    def test_paths_relative_to_config_not_current_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            path.write_text('root = "root"\ntrash = "trash"\n')
            cfg = load(path)
            self.assertEqual(cfg.root, (path.parent / 'root').resolve())
            self.assertEqual(cfg.trash, (path.parent / 'trash').resolve())

    def test_invalid_intervals_and_trash_locations_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'root'
            base = {'root': root, 'trash': Path(directory) / 'trash'}
            for changes in ({'role': 'other'}, {'poll_interval': 0}, {'poll_interval': float('inf')},
                            {'max_trash': -1}, {'max_trash': True}, {'fake_delay': -1},
                            {'trash': root}, {'trash': root / 'music' / 'trash'},
                            {'trash': root / 'library'}, {'canary_id': 'bad'}, {'notify_url': 'file:///tmp/file'}):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    Config(**(base | changes))

    def test_missing_or_unknown_settings_have_helpful_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            for content in ('role = "server"\n', 'root = "root"\ntypo = 2\n'):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    load(path)
