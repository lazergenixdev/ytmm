import subprocess
import unittest
from unittest.mock import Mock, patch

from ytmm.sources import Source, SourceError, metadata, read_clipboard, reference


A = "aaaaaaaaaaa"
B = "bbbbbbbbbbb"


class TestReferences(unittest.TestCase):
    def test_video_urls_and_id(self):
        for value in (A, f"https://youtu.be/{A}?si=share", f"https://music.youtube.com/watch?v={A}",
                      f"https://www.youtube.com/shorts/{A}", f"youtube.com/watch?v={A}"):
            self.assertEqual(reference(value), ("song", A))

    def test_playlist_takes_priority_over_watch_id(self):
        self.assertEqual(reference(f"https://music.youtube.com/watch?v={A}&list=PLsongs"), ("playlist", "PLsongs"))
        self.assertEqual(reference("https://music.youtube.com/browse/MPREb_album"), ("album", "MPREb_album"))

    def test_unknown_urls_are_never_sent_to_the_api(self):
        for value in (f"https://example.com/watch?v={A}", f"https://youtube.com.evil.org/watch?v={A}",
                      f"https://youtube.com@evil.org/watch?v={A}", f"file:///watch?v={A}", "artist song",
                      f"https://youtu.be/{A}extra"):
            with self.subTest(value=value), self.assertRaises(SourceError):
                reference(value)


class TestSources(unittest.TestCase):
    def test_song_prefers_recording_metadata_over_uploader(self):
        api = Mock()
        api.get_song.return_value = {"videoDetails": {"videoId": A, "title": "Upload title", "author": "Uploader"}}
        api.get_watch_playlist.return_value = {"tracks": [
            {"videoId": B, "title": "Unrelated"},
            {"videoId": A, "title": "Real title", "artists": [{"name": "Artist"}, {"name": "Guest"}],
             "album": {"name": "Album"}, "year": "2024", "thumbnail": [{"url": "https://image.test/cover"}]},
        ]}
        fields = Source(api).song(A)
        self.assertEqual(fields, {"title": "Real title", "artists": ["Artist", "Guest"], "album": "Album",
                                  "year": 2024, "cover_url": "https://image.test/cover"})

    def test_song_works_when_watch_metadata_unavailable(self):
        api = Mock()
        api.get_song.return_value = {"videoDetails": {"videoId": A, "title": "Title", "author": "Artist - Topic"}}
        api.get_watch_playlist.side_effect = RuntimeError("no watch list")
        self.assertEqual(Source(api).song(A), {"title": "Title", "artists": ["Artist"]})

    def test_unavailable_song_error_explains_reason(self):
        api = Mock()
        api.get_song.return_value = {"playabilityStatus": {"reason": "Video unavailable"}}
        api.get_watch_playlist.return_value = {"tracks": []}
        with self.assertRaisesRegex(SourceError, "Video unavailable"):
            Source(api).song(A)

    def test_song_can_use_watch_metadata_when_player_endpoint_fails(self):
        api = Mock()
        api.get_song.side_effect = RuntimeError("player unavailable")
        api.get_watch_playlist.return_value = {"tracks": [{"videoId": A, "title": "Song", "artists": [{"name": "Band"}]}]}
        self.assertEqual(Source(api).song(A), {"title": "Song", "artists": ["Band"]})

    def test_playlist_reads_all_tracks_skips_unavailable_and_duplicates(self):
        api = Mock()
        api.get_playlist.return_value = {"title": "Playlist", "year": "2026", "tracks": [
            {"videoId": A, "title": "First", "artists": [{"name": "A"}], "album": {"name": "Album"}},
            {"videoId": None, "title": "Deleted video"},
            {"videoId": B, "title": "Unavailable", "isAvailable": False},
            {"videoId": A, "title": "Duplicate"},
        ]}
        rows = Source(api).resolve(f"https://youtube.com/watch?v={B}&list=PLsongs")
        api.get_playlist.assert_called_once_with("PLsongs", limit=None)
        self.assertEqual(rows, [(A, {"title": "First", "artists": ["A"], "album": "Album"})])
        self.assertNotIn("year", rows[0][1])  # playlist creation year is not a recording year

    def test_album_inherits_metadata_and_preserves_track_position(self):
        api = Mock()
        api.get_album_browse_id.return_value = "MPREb_album"
        api.get_album.return_value = {"title": "Album", "year": "2020", "artists": [{"name": "Band"}],
                                      "thumbnails": [{"url": "https://image.test/large", "width": 1000, "height": 1000}],
                                      "tracks": [{"videoId": A, "title": "Unavailable", "isAvailable": False},
                                                 {"videoId": B, "title": "Second", "artists": None}]}
        rows = Source(api).playlist("https://music.youtube.com/playlist?list=OLAK5uy_album")
        api.get_album.assert_called_once_with("MPREb_album")
        self.assertEqual(rows, [(B, {"title": "Second", "artists": ["Band"], "album": "Album", "year": 2020,
                                    "track": 2, "cover_url": "https://image.test/large"})])

    def test_search_filters_results_and_normalizes_fields(self):
        api = Mock()
        api.search.return_value = [
            {"videoId": A, "title": "Result", "artists": [{"name": "Artist"}], "year": "2021", "trackNumber": "3"},
            {"title": "Not a song", "browseId": "MPREb_album"},
            {"videoId": A, "title": "Duplicate"},
            {"videoId": B, "title": "Unavailable", "isAvailable": False},
        ]
        rows = Source(api).search(" artist song ", limit=5)
        api.search.assert_called_once_with("artist song", filter="songs", limit=5)
        self.assertEqual(rows, [(A, {"title": "Result", "artists": ["Artist"], "year": 2021, "track": 3})])

    def test_api_errors_are_readable(self):
        api = Mock()
        api.get_playlist.side_effect = RuntimeError("network down")
        with self.assertRaisesRegex(SourceError, "network down"):
            Source(api).playlist("PLsongs")

    def test_metadata_chooses_largest_cover_and_ignores_invalid_fields(self):
        fields = metadata({"title": " Song ", "artists": [{"name": "Artist"}, {"name": "Artist"}, {}],
                           "year": "unknown", "trackNumber": -1, "thumbnails": [
                               {"url": "file:///private/photo", "width": 10000, "height": 10000},
                               {"url": "https://image.test/small", "width": 10, "height": 10},
                               {"url": "https://image.test/large", "width": 100, "height": 100}]})
        self.assertEqual(fields, {"title": "Song", "artists": ["Artist"], "cover_url": "https://image.test/large"})

    def test_resolve_does_not_load_client_for_bad_url(self):
        source = Source()
        with self.assertRaises(SourceError):
            source.resolve(f"https://example.com/watch?v={A}")
        self.assertIsNone(source._client)


class TestClipboard(unittest.TestCase):
    @patch("ytmm.sources.sys.platform", "darwin")
    @patch("ytmm.sources.shutil.which", return_value="/usr/bin/pbpaste")
    @patch("ytmm.sources.subprocess.run")
    def test_reads_native_clipboard(self, run, which):
        run.return_value = subprocess.CompletedProcess(["pbpaste"], 0, stdout=f" https://youtu.be/{A}\n")
        self.assertEqual(read_clipboard(), f"https://youtu.be/{A}")
        self.assertEqual(run.call_args.args[0], ["pbpaste"])
        self.assertNotIn("shell", run.call_args.kwargs)

    @patch("ytmm.sources.sys.platform", "linux")
    @patch("ytmm.sources.shutil.which", return_value=None)
    def test_missing_clipboard_tool_has_actionable_message(self, which):
        with self.assertRaisesRegex(SourceError, "pass a URL directly"):
            read_clipboard()


if __name__ == "__main__":
    unittest.main()
