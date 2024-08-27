import unittest
import logging
import ytmm

class TestYoutubeMM(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.basicConfig(level=logging.INFO)  # Configure logging to capture INFO level logs

    def setUp(self):
        pass
    #    self.manager = ytmm.YoutubeMM()
    #    self.manager.add({
    #        "id": "abcde_12345",
    #        "title": "test",
    #        "artists": ["name1", "name2"],
    #        "location": "album_name",
    #    })

    def test_add(self):
        pass
    #    self.manager.add('https://youtube.com/anothervideo')
    #    self.assertIn('https://youtube.com/anothervideo', self.manager.entries)

    def test_sync(self):
        pass
    #    # Assuming sync just prints entries, this is a simple test
    #    with self.assertLogs('root', level='INFO') as cm:
    #        self.manager.sync('output_directory')
    #    item = "{'id': 'abcde_12345', 'title': 'test', 'artists': ['name1', 'name2'], 'location': 'album_name'}"
    #    self.assertIn(item, cm.output[0])

if __name__ == '__main__':
    unittest.main()
