"""Client command integration and real terminal picker interactions, offline."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from rich.console import Console

from ytmm import cli, config
from ytmm.config import Config
from ytmm.library import Library
from ytmm.plan import file_name
from ytmm.selection import find, pick
from ytmm.sources import SourceError


A = 'aaaaaaaaaaa'
B = 'bbbbbbbbbbb'
C = 'ccccccccccc'


class TestClientCLI(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.cfg = Config(root=self.directory / 'root', device='laptop', trash=self.directory / 'trash')
        self.cfg.library_dir.mkdir(parents=True)
        self.cfg.songs_dir.mkdir(parents=True)
        self.path = self.directory / 'config.toml'
        config.save(self.cfg, self.path)
        Library(self.cfg.library_dir, self.cfg.device).save()
        environment = patch.dict(os.environ, {'YTMM_CONFIG': str(self.path)})
        environment.start()
        self.addCleanup(environment.stop)
        self.output = io.StringIO()
        console = patch.object(cli, 'console', Console(file=self.output, width=180, color_system=None))
        console.start()
        self.addCleanup(console.stop)

    def library(self):
        return Library(self.cfg.library_dir, self.cfg.device).load()

    def add(self, video_id=A, title='Alpha', artists='Band', album='Record'):
        self.assertEqual(cli.main(['add', video_id, '--title', title, '--artist', artists, '--album', album]), 0)

    def test_explicit_metadata_add_is_offline_and_does_not_write_audio(self):
        with (patch('ytmm.sources.Source.resolve', side_effect=AssertionError('network request')),
              patch('ytmm.sources.Source.search', side_effect=AssertionError('network request'))):
            self.assertEqual(cli.main(['add', f'https://youtu.be/{A}', '--title', 'Song', '--artist', 'Band, Guest',
                                       '--album', 'Record', '--year', '2020']), 0)
        song = self.library().songs[A]
        self.assertEqual((song['title'], song['artists'], song['album'], song['year']),
                         ('Song', ['Band', 'Guest'], 'Record', 2020))
        self.assertEqual(list(self.cfg.songs_dir.iterdir()), [])

    def test_duplicate_add_preserves_manual_edits(self):
        self.add(title='Original')
        before = self.library().songs[A]
        self.add(title='New title')
        self.assertEqual(self.library().songs[A], before)
        self.assertIn('skipped 1', self.output.getvalue())

    def test_deleted_song_requires_explicit_restore(self):
        self.add()
        self.assertEqual(cli.main(['rm', A]), 0)
        tombstone = self.library().songs[A]
        self.assertTrue(tombstone['deleted'])
        self.add(title='Accidental resurrection')
        self.assertEqual(self.library().songs[A], tombstone)
        self.assertEqual(cli.main(['add', A, '--title', 'Restored', '--restore']), 0)
        restored = self.library().songs[A]
        self.assertNotIn('deleted', restored)
        self.assertEqual(restored['title'], 'Restored')
        self.assertGreater(restored['updated'], tombstone['updated'])

    def test_edit_supports_all_metadata_fields_and_only_edits_library(self):
        self.add()
        old_audio = self.cfg.songs_dir / file_name(A, self.library().songs[A])
        old_audio.write_bytes(b'audio untouched by client')
        self.assertEqual(cli.main(['edit', A, '--title', 'New song', '--artist', 'Guest, Band',
                                  '--album', 'New album', '--year', '2024']), 0)
        song = self.library().songs[A]
        self.assertEqual((song['title'], song['artists'], song['album'], song['year']),
                         ('New song', ['Guest', 'Band'], 'New album', 2024))
        self.assertEqual(old_audio.read_bytes(), b'audio untouched by client')

    def test_ambiguous_removal_requires_picker_or_explicit_all(self):
        self.add(A, title='Alpha song')
        self.add(B, title='Beta song')
        with patch('ytmm.selection.sys.stdin.isatty', return_value=False):
            self.assertEqual(cli.main(['rm', 'song']), 1)
        self.assertEqual(len(self.library().live()), 2)
        self.assertIn('multiple matches require a terminal picker', self.output.getvalue())
        self.assertEqual(cli.main(['rm', 'song', '--all']), 0)
        self.assertEqual(self.library().live(), {})
        self.assertTrue(all(song['deleted'] for song in self.library().songs.values()))

    def test_clear_metadata_fields_and_render_literal_markup(self):
        self.add(title='[unknown-tag]Song[/unknown-tag]')
        self.assertEqual(cli.main(['edit', A, '--album', '', '--artist', '', '--year', '0']), 0)
        song = self.library().songs[A]
        self.assertEqual((song['album'], song['artists'], song['year']), ('', [], ''))
        self.assertEqual(cli.main(['ls']), 0)
        self.assertIn('[unknown-tag]Song[/unknown-tag]', self.output.getvalue())

    def test_ambiguous_edit_applies_only_picker_choices(self):
        self.add(A, title='Alpha song')
        self.add(B, title='Beta song')
        with patch('ytmm.cli.pick', return_value=[B]) as choose:
            self.assertEqual(cli.main(['edit', 'song', '--album', 'Changed']), 0)
        self.assertEqual(set(choose.call_args.args[1]), {A, B})
        self.assertEqual(self.library().songs[A]['album'], 'Record')
        self.assertEqual(self.library().songs[B]['album'], 'Changed')

    def test_cancelled_picker_leaves_entries_identical(self):
        self.add(A, title='Alpha song')
        self.add(B, title='Beta song')
        before = self.library().songs
        with patch('ytmm.cli.pick', return_value=[]):
            self.assertEqual(cli.main(['rm', 'song']), 0)
        self.assertEqual(self.library().songs, before)

    def test_listing_fuzzy_search_matches_artist_album_and_title_together(self):
        self.add(A, title='Around the World', artists='Daft Punk', album='Homework')
        self.add(B, title='Other song', artists='Other band', album='Other record')
        self.assertEqual(cli.main(['ls', 'arnd', 'dft', 'hmwrk', '--count']), 0)
        self.assertEqual(self.output.getvalue().splitlines()[-1], '1')
        self.assertEqual(cli.main(['query', '--regex', '^around.*Daft.*Homework', '--count']), 0)
        self.assertEqual(self.output.getvalue().splitlines()[-1], '1')

    def test_regex_errors_return_one_without_changing_library(self):
        self.add()
        before = self.library().songs
        self.assertEqual(cli.main(['rm', '--regex', '[']), 1)
        self.assertEqual(self.library().songs, before)
        self.assertIn('Error:', self.output.getvalue())

    def test_tag_round_trip(self):
        self.add()
        self.assertEqual(cli.main(['tag', 'gym', A]), 0)
        self.assertEqual(self.library().songs[A]['tags'], ['gym'])
        self.assertEqual(cli.main(['ls', 'gym', '--count']), 0)
        self.assertEqual(self.output.getvalue().splitlines()[-1], '1')
        self.assertEqual(cli.main(['tag', 'gym', A, '--remove']), 0)
        self.assertEqual(self.library().songs[A]['tags'], [])

    def test_playlist_url_dispatch_imports_all_resolved_entries(self):
        url = f'https://music.youtube.com/watch?v={A}&list=PLsongs'
        rows = [(A, {'title': 'First', 'artists': ['Band']}), (B, {'title': 'Second', 'artists': []})]
        with (patch('ytmm.sources.Source.resolve', return_value=rows) as resolve,
              patch('ytmm.sources.Source.search', side_effect=AssertionError('unexpected search'))):
            self.assertEqual(cli.main(['add', url]), 0)
        resolve.assert_called_once_with(url)
        self.assertEqual(set(self.library().live()), {A, B})

    def test_search_add_requires_selection_and_imports_only_selected_song(self):
        rows = [(A, {'title': 'First', 'artists': ['Band']}), (B, {'title': 'Second', 'artists': []})]
        with (patch('ytmm.sources.Source.search', return_value=rows) as search,
              patch('ytmm.cli.pick', return_value=[B])):
            self.assertEqual(cli.main(['add', 'artist', 'song']), 0)
        search.assert_called_once_with('artist song')
        self.assertEqual(set(self.library().live()), {B})

    def test_clipboard_accepts_multiple_urls_and_rejects_non_sources(self):
        rows = {f'https://youtu.be/{A}': [(A, {'title': 'First', 'artists': []})],
                f'https://youtu.be/{B}': [(B, {'title': 'Second', 'artists': []})]}
        with (patch('ytmm.sources.read_clipboard', return_value='\n'.join(rows)),
              patch('ytmm.sources.Source.resolve', side_effect=lambda url: rows[url])):
            self.assertEqual(cli.main(['add']), 0)
        self.assertEqual(set(self.library().live()), {A, B})
        before = self.library().songs
        with patch('ytmm.sources.read_clipboard', return_value='unrelated clipboard text'):
            self.assertEqual(cli.main(['add']), 1)
        self.assertEqual(self.library().songs, before)

    def test_source_failure_returns_one_without_partial_import(self):
        with patch('ytmm.sources.Source.resolve', side_effect=SourceError('network down')):
            self.assertEqual(cli.main(['add', A]), 1)
        self.assertEqual(self.library().songs, {})
        self.assertIn('network down', self.output.getvalue())

    def test_missing_config_and_missing_matches_return_one(self):
        self.assertEqual(cli.main(['rm', 'missing']), 1)
        self.path.unlink()
        self.assertEqual(cli.main(['ls']), 1)
        self.assertIn('run `ytmm init` first', self.output.getvalue())

    def test_init_is_explicit_about_replacing_device_config(self):
        original = self.path.read_bytes()
        self.assertEqual(cli.main(['init', str(self.directory / 'other')]), 1)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(cli.main(['init', str(self.directory / 'other'), '--force', '--name', 'desktop']), 0)
        loaded = config.load(self.path)
        self.assertEqual(loaded.device, 'desktop')
        self.assertTrue((loaded.library_dir / 'library.json').exists())

    def test_migration_dry_run_preserves_database_audio_and_shared_library(self):
        self.cfg.role = 'server'
        config.save(self.cfg, self.path)
        legacy_dir = self.directory / 'old'
        legacy_dir.mkdir()
        audio = legacy_dir / 'old_song.mp3'
        audio.write_bytes(b'original audio')
        source = self.directory / 'music.json'
        source.write_text(json.dumps({'root': str(legacy_dir), 'data': [
            {'id': A, 'title': 'Old Song', 'artists': ['Band']}]}), encoding='utf-8')
        before = {path: path.read_bytes() for path in (source, audio, self.cfg.library_dir / 'library.json')}
        self.assertEqual(cli.main(['migrate', str(source), '--dry-run']), 0)
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertEqual(list(self.cfg.songs_dir.iterdir()), [])
        self.assertIn('Dry run: 1 added, 1 files moved', self.output.getvalue())

    def test_client_commands_do_not_import_server_dependencies(self):
        # A fresh interpreter catches eager imports even if another test imported
        # the worker or download modules earlier in this process.
        program = '''
import sys
from ytmm.cli import main
assert main(['ls', '--count']) == 0
assert main(['status']) == 0
assert not any(name.split('.')[0] in ('yt_dlp', 'mutagen') for name in sys.modules)
'''
        result = subprocess.run([sys.executable, '-c', program], capture_output=True, text=True,
                                env=os.environ.copy(), timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


class TestSelection(unittest.TestCase):
    songs = {
        A: {'title': 'Alpha song', 'artists': ['Band'], 'album': 'First record'},
        B: {'title': 'Beta song', 'artists': ['Other'], 'album': 'Second record'},
        C: {'title': 'Gamma', 'artists': ['Band'], 'album': 'Last record'},
    }

    def test_fuzzy_matching_is_case_insensitive_and_combines_fields(self):
        self.assertEqual(find(self.songs, 'ALPH bnd frst'), [A])
        self.assertEqual(find(self.songs, 'bt scnd'), [B])
        self.assertEqual(find(self.songs, 'missing'), [])

    def test_explicit_id_and_url_select_exactly_one_song(self):
        self.assertEqual(find(self.songs, B), [B])
        self.assertEqual(find(self.songs, f'https://youtu.be/{B}'), [B])

    def test_regex_is_opt_in(self):
        self.assertEqual(find(self.songs, '^alpha', regex=True), [A])
        self.assertEqual(find(self.songs, '^alpha'), [])

    def picker(self, keys):
        try:
            from prompt_toolkit.input import create_pipe_input
            from prompt_toolkit.output import DummyOutput
        except ImportError:
            self.skipTest('prompt-toolkit is not installed')
        with create_pipe_input() as pipe:
            pipe.send_text(keys)
            return pick(self.songs, [A, B, C], input=pipe, output=DummyOutput())

    def test_picker_filters_as_you_type_and_accepts_highlighted_song(self):
        self.assertEqual(self.picker('beta\r'), [B])

    def test_picker_keeps_multiple_selections_across_filter_changes(self):
        self.assertEqual(self.picker('alpha\t' + '\x7f' * 5 + 'beta\t\r'), [A, B])

    def test_picker_navigation_selects_current_row(self):
        self.assertEqual(self.picker('\x1b[B\r'), [B])

    def test_picker_cancel_returns_no_selection(self):
        self.assertEqual(self.picker('\x03'), [])

    def test_picker_no_matches_does_not_fall_back_to_an_unrelated_song(self):
        self.assertEqual(self.picker('unmatched\r'), [])


if __name__ == '__main__':
    unittest.main()
