import unittest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from ytmm.library import search
from ytmm.picker import pick

SONGS = {
    'a': {'title': 'One More Time', 'artists': ['Daft Punk']},
    'b': {'title': 'Around the World', 'artists': ['Daft Punk']},
    'c': {'title': 'Halo', 'artists': ['Beyoncé']},
}
ITEMS = [(id, [('', s['title'])]) for id, s in SONGS.items()]
UP, DOWN, TAB, ENTER, ESC, CTRL_A = '\x1b[A', '\x1b[B', '\t', '\r', '\x1b', '\x01'


def run(keys: str, query: str = '', multi: bool = True):
    with create_pipe_input() as inp:
        inp.send_text(keys)
        return pick(ITEMS, lambda q: search(SONGS, q, best=False), query, multi, input=inp, output=DummyOutput())


class TestPicker(unittest.TestCase):
    def test_enter_takes_the_highlighted_song(self):
        self.assertEqual(run(ENTER), ['a'])
        self.assertEqual(run(DOWN + DOWN + ENTER), ['c'])
        self.assertEqual(run(DOWN + DOWN + DOWN + UP + ENTER), ['b'])

    def test_typing_filters(self):
        self.assertEqual(run('halo' + ENTER), ['c'])
        self.assertEqual(run('beyonce' + ENTER), ['c'])
        self.assertEqual(run(ENTER, query='world'), ['b'])

    def test_tab_selects_several(self):
        self.assertEqual(run(TAB + DOWN + TAB + ENTER), ['a', 'c'])
        self.assertEqual(run('daft' + CTRL_A + ENTER), ['a', 'b'])
        self.assertEqual(run('daft' + CTRL_A + CTRL_A + ENTER), ['a'])  # toggled off: the highlighted one

    def test_selection_survives_filtering(self):
        self.assertEqual(run('halo' + TAB + '\x7f' * 4 + 'daft' + TAB + ENTER), ['a', 'c'])

    def test_tab_is_ignored_without_multi(self):
        self.assertEqual(run(TAB + DOWN + ENTER, multi=False), ['b'])

    def test_cancel(self):
        self.assertIsNone(run(ESC))

    def test_no_matches(self):
        self.assertEqual(run('zzzz' + ENTER), [])


if __name__ == '__main__':
    unittest.main()
