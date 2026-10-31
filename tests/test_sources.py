import unittest
from ytmm import sources
from ytmm.sources import Track, best_cover, classify, find_urls, inbox_new, split_title, to_track

ART = 'https://yt3.googleusercontent.com/12uePL7EChVz'

# shapes recorded from ytmusicapi 1.12
WATCH_ATV = {
    'videoId': 'YQiau5iKLHc', 'title': 'Sleepwalker', 'length': '3:11',
    'thumbnail': [{'url': f'{ART}=w60-h60-l90-rj', 'width': 60, 'height': 60},
                  {'url': f'{ART}=w544-h544-l90-rj', 'width': 544, 'height': 544}],
    'videoType': 'MUSIC_VIDEO_TYPE_ATV',
    'artists': [{'name': 'Ava Max', 'id': 'UCNCrZy3cD0_qZfFz2f6gSkQ'}],
    'album': {'name': 'Diamonds & Dancefloors', 'id': 'MPREb_x509Y5ht4ih'}, 'year': '2023',
}
WATCH_OMV = {
    'videoId': 'dQw4w9WgXcQ', 'title': 'Never Gonna Give You Up', 'length': '3:34',
    'thumbnail': [{'url': 'https://i.ytimg.com/vi/dQw4w9WgXcQ/hq720.jpg?sqp=x', 'width': 800, 'height': 450}],
    'videoType': 'MUSIC_VIDEO_TYPE_OMV', 'artists': [{'name': 'Rick Astley', 'id': 'UCwZEU0wAwIyZb4x5G_KJp2w'}],
    'views': '19M',
}
SEARCH_SONG = {
    'category': 'Songs', 'resultType': 'song', 'title': 'One More Time (Radio Edit)',
    'album': {'name': 'One More Time', 'id': 'MPREb_uWQeJO6Z53u'}, 'videoId': 'wU26xVT_vBU',
    'videoType': 'MUSIC_VIDEO_TYPE_ATV', 'isAvailable': True, 'duration': '5:21', 'year': None,
    'artists': [{'name': 'Daft Punk', 'id': 'UCRr1xG_2WIDs18a6cIiCxeA'}],
    'thumbnails': [{'url': f'{ART}=w60-h60-l90-rj', 'width': 60, 'height': 60}],
}


class FakeYT:
    def __init__(self):
        self.calls = []

    def get_watch_playlist(self, videoId, limit):
        self.calls.append(('watch', videoId))
        if videoId == 'beethoven99':
            from ytmusicapi.exceptions import YTMusicServerError
            raise YTMusicServerError('No content returned by the server.')
        return {'tracks': [WATCH_ATV if videoId == 'YQiau5iKLHc' else WATCH_OMV]}

    def get_album(self, browse_id):
        self.calls.append(('album', browse_id))
        return {
            'title': 'Discovery', 'year': '2001', 'artists': [{'name': 'Daft Punk', 'id': 'x'}],
            'audioPlaylistId': 'OLAK5uy_discovery00000',
            'thumbnails': [{'url': f'{ART}=w544-h544-l90-rj', 'width': 544, 'height': 544}],
            'tracks': [{'videoId': 'FGBhQbmPwH8', 'title': 'One More Time', 'artists': [{'name': 'Daft Punk'}],
                        'album': 'Discovery', 'thumbnails': None, 'videoType': 'MUSIC_VIDEO_TYPE_OMV'}],
        }

    def get_album_browse_id(self, list_id):
        return 'MPREb_7ltM34kr0mH'

    def get_playlist(self, list_id, limit):
        self.calls.append(('playlist', list_id))
        if list_id.startswith('OLAK5uy_'):
            return {'title': 'Discovery', 'tracks': [
                {'videoId': 'fa5IWHDbftI', 'title': 'One More Time', 'artists': [{'name': 'Daft Punk'}],
                 'album': {'name': 'Discovery', 'id': 'MPREb_7ltM34kr0mH'}, 'isAvailable': True,
                 'videoType': 'MUSIC_VIDEO_TYPE_ATV', 'thumbnails': []},
                {'videoId': 'gone0000000', 'title': 'Unavailable', 'isAvailable': False},
            ]}
        return {'title': 'Workout', 'tracks': [WATCH_OMV, {**SEARCH_SONG, 'year': None}]}

    def search(self, query, filter, limit):
        self.calls.append(('search', query, filter))
        return [SEARCH_SONG] * 20


class SourcesTest(unittest.TestCase):
    def setUp(self):
        self.yt = sources._yt = FakeYT()
        sources._album.cache_clear()

    def tearDown(self):
        sources._yt = None
        sources._album.cache_clear()


class TestClassify(unittest.TestCase):
    def test_kinds(self):
        cases = {
            'https://music.youtube.com/watch?v=YQiau5iKLHc&list=RDAMVMYQiau5iKLHc': ('video', 'YQiau5iKLHc'),
            'https://youtu.be/dQw4w9WgXcQ': ('video', 'dQw4w9WgXcQ'),
            'dQw4w9WgXcQ': ('video', 'dQw4w9WgXcQ'),
            'https://music.youtube.com/playlist?list=OLAK5uy_mz6eafmqdRHSaR4IwG0ll6J6rgv0_ZpGw':
                ('playlist', 'OLAK5uy_mz6eafmqdRHSaR4IwG0ll6J6rgv0_ZpGw'),
            'https://www.youtube.com/watch?list=PL5sCQfX0zpD2S4foifinyLwSljBhn_Sqz': ('playlist', 'PL5sCQfX0zpD2S4foifinyLwSljBhn_Sqz'),
            'VLPL5sCQfX0zpD2S4foifinyLwSljBhn_Sqz': ('playlist', 'PL5sCQfX0zpD2S4foifinyLwSljBhn_Sqz'),
            'https://music.youtube.com/browse/MPREb_7ltM34kr0mH': ('album', 'MPREb_7ltM34kr0mH'),
            'daft punk': (None, 'daft punk'),
            'https://example.com/watch': (None, 'https://example.com/watch'),
        }
        for arg, want in cases.items():
            self.assertEqual(classify(arg), want, arg)

    def test_find_urls(self):
        text = ('check this (https://youtu.be/dQw4w9WgXcQ), and https://example.com/x\n'
                'https://music.youtube.com/playlist?list=PL5sCQfX0zpD2S4foifinyLwSljBhn_Sqz https://youtu.be/dQw4w9WgXcQ')
        self.assertEqual(find_urls(text), ['https://youtu.be/dQw4w9WgXcQ',
                                           'https://music.youtube.com/playlist?list=PL5sCQfX0zpD2S4foifinyLwSljBhn_Sqz'])
        self.assertEqual(find_urls(' dQw4w9WgXcQ\n'), ['dQw4w9WgXcQ'])
        self.assertEqual(find_urls('nothing here'), [])


class TestTracks(unittest.TestCase):
    def test_album_track(self):
        t = to_track(WATCH_ATV)
        self.assertEqual(t.fields(), {'title': 'Sleepwalker', 'artists': ['Ava Max'], 'album': 'Diamonds & Dancefloors',
                                      'year': 2023, 'cover': f'{ART}=w720-h720-l90-rj'})
        self.assertEqual((t.album_id, t.duration, t.video), ('MPREb_x509Y5ht4ih', '3:11', False))

    def test_music_video_has_no_cover(self):
        t = to_track(WATCH_OMV)
        self.assertEqual(t.fields(), {'title': 'Never Gonna Give You Up', 'artists': ['Rick Astley']})
        self.assertTrue(t.video)

    def test_plain_upload_title_is_split(self):
        t = to_track({**WATCH_OMV, 'videoType': 'MUSIC_VIDEO_TYPE_UGC', 'title': 'Daft Punk - Veridis Quo (Official Audio)',
                      'artists': [{'name': 'someone'}]})
        self.assertEqual((t.title, t.artists), ('Veridis Quo', ['Daft Punk']))

    def test_split_title(self):
        self.assertEqual(split_title('Song [Lyrics]', ['Artist - Topic']), ('Song', ['Artist']))
        self.assertEqual(split_title('A, B – Song (Official Music Video)', []), ('Song', ['A', 'B']))
        self.assertEqual(split_title('Just A Title', ['Uploader']), ('Just A Title', ['Uploader']))

    def test_unavailable(self):
        self.assertIsNone(to_track({'videoId': 'x' * 11, 'title': 't', 'isAvailable': False}))
        self.assertIsNone(to_track({'title': 'no id'}))

    def test_best_cover(self):
        self.assertIsNone(best_cover(WATCH_OMV['thumbnail']))
        self.assertIsNone(best_cover(None))


class TestLookups(SourcesTest):
    def test_lookup(self):
        self.assertEqual(sources.lookup('YQiau5iKLHc').title, 'Sleepwalker')
        self.assertIsNone(sources.lookup('beethoven99'))

    def test_search_limit(self):
        self.assertEqual(len(sources.search('daft punk', limit=10)), 10)
        self.assertEqual(self.yt.calls[-1], ('search', 'daft punk', 'songs'))
        sources.search('x', videos=True)
        self.assertEqual(self.yt.calls[-1][2], 'videos')

    def test_album_uses_its_audio_playlist(self):
        title, tracks = sources.album('MPREb_7ltM34kr0mH')
        self.assertEqual(title, 'Discovery (Daft Punk · 2001)')
        self.assertEqual([t.id for t in tracks], ['fa5IWHDbftI'])  # the album track, not the music video
        self.assertEqual(tracks[0].fields(), {'title': 'One More Time', 'artists': ['Daft Punk'], 'album': 'Discovery',
                                              'year': 2001, 'cover': f'{ART}=w720-h720-l90-rj'})

    def test_album_playlist_link_reads_the_album(self):
        title, tracks = sources.playlist('OLAK5uy_mz6eafmqdRHSaR4IwG0ll6J6rgv0_ZpGw')
        self.assertTrue(title.startswith('Discovery'))
        self.assertEqual(tracks[0].year, 2001)

    def test_complete_fills_years_once_per_album(self):
        _, tracks = sources.playlist('PL5sCQfX0zpD2S4foifinyLwSljBhn_Sqz')
        self.assertEqual([t.year for t in tracks], [None, None])
        sources.complete(tracks + tracks)
        self.assertEqual([t.year for t in tracks], [None, 2001])  # the music video has no album
        self.assertEqual(sum(1 for c in self.yt.calls if c[0] == 'album'), 1)


class TestInbox(unittest.TestCase):
    def test_inbox_new(self):
        songs = {'a' * 11: {'title': 'a'}, 'd' * 11: {'title': 'd', 'deleted': True}}
        tracks = [Track('a' * 11, 'a'), Track('d' * 11, 'd'), Track('n' * 11, 'n'), Track('n' * 11, 'n')]
        self.assertEqual([t.id for t in inbox_new(songs, None, tracks)], ['n' * 11])
        self.assertEqual([t.id for t in inbox_new(songs, ['a' * 11], tracks)], ['d' * 11, 'n' * 11])
        self.assertEqual(inbox_new(songs, ['a' * 11, 'd' * 11, 'n' * 11], tracks), [])


if __name__ == '__main__':
    unittest.main()
