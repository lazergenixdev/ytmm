"""Fuzzy matching and a small type-to-filter, multi-select terminal picker."""
import re
import sys

from .library import parse_id


def _subsequence(needle, haystack):
    it = iter(haystack)
    return all(any(c == candidate for candidate in it) for c in needle)


def find(songs: dict, query: str, *, regex=False) -> list[str]:
    id = parse_id(query)
    if id in songs:
        return [id]
    pattern = re.compile(query, re.IGNORECASE) if regex else None
    tokens = query.casefold().split()
    found = []
    for id, song in songs.items():
        text = ' '.join([song.get('title', ''), *song.get('artists', []), song.get('album') or '',
                         *song.get('tags', []), id])
        if (pattern.search(text) if pattern else all(_subsequence(t, text.casefold()) for t in tokens)):
            found.append(id)
    return sorted(found, key=lambda id: (songs[id].get('title', '').casefold(), id))


def pick(songs: dict, ids: list[str], *, title='Select songs', input=None, output=None) -> list[str]:
    if input is None and (not sys.stdin.isatty() or not sys.stdout.isatty()):
        raise ValueError('multiple matches require a terminal picker; use a video ID or --all')
    try:
        from prompt_toolkit.application import Application
        from prompt_toolkit.buffer import Buffer
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import HSplit, Layout, Window
        from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    except ImportError as exc:
        raise RuntimeError('the picker needs prompt-toolkit; reinstall ytmm') from exc

    chosen = set()
    visible = list(ids)
    cursor = [0]
    keys = KeyBindings()

    def changed(buffer):
        visible[:] = find({id: songs[id] for id in ids}, buffer.text)
        cursor[0] = 0

    query = Buffer(on_text_changed=changed)

    def rows():
        result = []
        start = max(0, cursor[0] - 8)
        for i in range(start, min(len(visible), start + 16)):
            id = visible[i]
            song = songs[id]
            line = (f'{">" if i == cursor[0] else " "} [{"x" if id in chosen else " "}] '
                    f'{", ".join(song.get("artists", []))} — {song.get("title", id)} '
                    f'({song.get("album", "")}) [{id}]\n')
            result.append(('reverse' if i == cursor[0] else '', line))
        if not visible:
            result.append(('', 'No matches.\n'))
        result.append(('', f'\n{len(chosen)} selected'))
        return result

    @keys.add('up')
    def up(event):
        cursor[0] = max(0, cursor[0] - 1)

    @keys.add('down')
    def down(event):
        cursor[0] = min(max(0, len(visible) - 1), cursor[0] + 1)

    @keys.add('tab')
    def toggle(event):
        if visible:
            chosen.symmetric_difference_update({visible[cursor[0]]})

    @keys.add('enter')
    def accept(event):
        result = chosen or ({visible[cursor[0]]} if visible else set())
        event.app.exit(result=[id for id in ids if id in result])

    @keys.add('c-c')
    @keys.add('escape')
    def cancel(event):
        event.app.exit(result=[])

    root = HSplit([
        Window(FormattedTextControl(f'{title}\nType to filter · ↑/↓ move · Tab select · Enter apply · Esc cancel'), height=2),
        Window(BufferControl(buffer=query), height=1),
        Window(FormattedTextControl(rows)),
    ])
    app = Application(layout=Layout(root, focused_element=root.children[1]), key_bindings=keys,
                      full_screen=True, input=input, output=output)
    return app.run()
