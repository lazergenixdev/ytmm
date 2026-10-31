import shutil, subprocess, tempfile, unittest
from pathlib import Path
from ytmm.download import error_message, meta_from_info, read_id, square, write_tags

ID = 'jNQXAC9IVRw'
FFMPEG = shutil.which('ffmpeg')


def ffmpeg(*args):
    subprocess.run([FFMPEG, '-v', 'error', '-y', *args], check=True)


def size(path: Path) -> tuple[int, int]:
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=width,height', '-of', 'csv=p=0', str(path)],
                         capture_output=True, text=True, check=True).stdout
    w, h = out.strip().split(',')
    return int(w), int(h)


class TestYtDlpOutput(unittest.TestCase):
    def test_error_message(self):
        stderr = ('WARNING: [youtube] something\n'
                  f'ERROR: [youtube] {ID}: Video unavailable. This video has been removed by the uploader\n')
        self.assertEqual(error_message(stderr, ID), 'Video unavailable. This video has been removed by the uploader')
        self.assertEqual(error_message('', ID), 'yt-dlp failed')

    def test_meta_from_album_track(self):
        info = {'track': 'Sleepwalker', 'artists': ['Ava Max'], 'album': 'Diamonds & Dancefloors', 'release_year': 2023}
        self.assertEqual(meta_from_info(info), {'title': 'Sleepwalker', 'artists': ['Ava Max'],
                                                'album': 'Diamonds & Dancefloors', 'year': 2023})

    def test_meta_from_plain_video(self):
        self.assertEqual(meta_from_info({'title': 'Me at the zoo', 'uploader': 'jawed'}),
                         {'title': 'Me at the zoo', 'artists': ['jawed']})
        self.assertEqual(meta_from_info({'title': 'Artist - Song (Official Video)', 'uploader': 'x'}),
                         {'title': 'Song', 'artists': ['Artist']})
        self.assertEqual(meta_from_info({}), {})


@unittest.skipUnless(FFMPEG, 'needs ffmpeg')
class TestTags(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        cover = self.dir / 'cover.jpg'
        ffmpeg('-f', 'lavfi', '-i', 'color=c=red:s=64x64', '-frames:v', '1', str(cover))
        self.cover = cover.read_bytes()

    def tearDown(self):
        self.tmp.cleanup()

    def audio(self, ext: str, *codec) -> Path:
        path = self.dir / f'a{ext}'
        ffmpeg('-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=mono', '-t', '1', *codec, str(path))
        return path

    def check(self, path: Path):
        import mutagen
        song = {'title': 'Sleepwalker', 'artists': ['Ava Max', 'Someone'], 'album': 'Diamonds', 'year': 2023}
        write_tags(path, ID, song, self.cover)
        write_tags(path, ID, {**song, 'title': 'Sleepwalker 2'}, self.cover)  # retag replaces, doesn't add
        f = mutagen.File(path, easy=True)
        self.assertEqual(f['title'], ['Sleepwalker 2'])
        self.assertIn(f['artist'], (['Ava Max, Someone'], ['Ava Max', 'Someone']))
        self.assertEqual(f['album'], ['Diamonds'])
        self.assertEqual(f['date'], ['2023'])
        self.assertEqual(read_id(path), ID)
        return mutagen.File(path)

    def test_mp3(self):
        path = self.audio('.mp3', '-q:a', '9')
        from mutagen.id3 import ID3, TXXX
        tags = ID3()
        tags.add(TXXX(encoding=3, desc='description', text='https://www.youtube.com/watch?v=0000000000x'))
        tags.save(path)
        f = self.check(path)
        self.assertEqual(f.tags.version[:2], (2, 3))
        self.assertEqual(f.tags.getall('APIC')[0].data, self.cover)
        self.assertEqual(f.tags.getall('TXXX:description'), [])  # old tags are gone

    def test_m4a(self):
        f = self.check(self.audio('.m4a', '-c:a', 'aac'))
        self.assertEqual(bytes(f.tags['covr'][0]), self.cover)

    def test_opus(self):
        self.check(self.audio('.opus', '-c:a', 'libopus'))

    def test_read_id_from_yt_dlp_metadata(self):
        """v0.2 files: yt-dlp put the URL in purl and comment, and other URLs in the description."""
        path = self.audio('.mp3', '-q:a', '9')
        from mutagen.id3 import ID3, TXXX
        tags = ID3()
        tags.add(TXXX(encoding=3, desc='description', text='https://www.youtube.com/watch?v=0000000000x'))
        tags.add(TXXX(encoding=3, desc='purl', text=f'https://www.youtube.com/watch?v={ID}'))
        tags.save(path)
        self.assertEqual(read_id(path), ID)

    def test_read_id_without_tags(self):
        self.assertIsNone(read_id(self.audio('.mp3', '-q:a', '9')))
        junk = self.dir / 'junk.mp3'
        junk.write_bytes(b'not audio')
        self.assertIsNone(read_id(junk))

    def test_square_cuts_out_pillarboxed_art(self):
        for w, h, side in [(1280, 720, 720), (480, 360, 270)]:
            thumb = self.dir / f'{w}.jpg'
            ffmpeg('-f', 'lavfi', '-i', f'color=c=blue:s={w}x{h}', '-frames:v', '1', str(thumb))
            out = self.dir / f'{w}-square.jpg'
            out.write_bytes(square(thumb.read_bytes()))
            self.assertEqual(size(out), (side, side))


if __name__ == '__main__':
    unittest.main()
