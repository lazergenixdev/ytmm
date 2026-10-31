"""
Interactive picker: type to filter, tab to select several, enter to accept.
Opens when a command matches more than one song, and for search results.
"""
from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.styles import Style

MAX_ROWS = 15

STYLE = Style.from_dict({
    'prompt':   'ansicyan bold',
    'message':  'ansicyan',
    'current':  'reverse',
    'selected': 'ansigreen bold',
    'artist':   'italic #d75fd7',
    'title':    '#00ffaf',
    'tag':      '#5f87ff',
    'ok':       'ansigreen',
    'dim':      '#808080',
})


def pick(items, rank, query='', multi=True, message=None, input=None, output=None) -> list[str] | None:
    """
    items: [(key, label)], label being prompt_toolkit formatted text ([(style, text)])
    rank:  query -> keys matching it, best first
    Returns the selected keys in list order (the highlighted one if none were
    selected), [] if nothing matched, or None if cancelled.
    """
    labels = dict(items)
    order = [k for k, _ in items]
    view = {'keys': order, 'cursor': 0, 'top': 0}
    selected = set()
    rows = max(1, min(MAX_ROWS, len(order)))

    def refilter(_=None):
        text = buffer.text
        try:
            keys = rank(text) if text.strip() else order
        except Exception:  # e.g. a regex that isn't finished yet
            keys = []
        view['keys'] = [k for k in keys if k in labels]
        view['cursor'] = view['top'] = 0

    buffer = Buffer(document=Document(query, len(query)), multiline=False, on_text_changed=refilter)
    refilter()

    def move(n):
        if view['keys']:
            view['cursor'] = max(0, min(len(view['keys']) - 1, view['cursor'] + n))

    def lines():
        keys, cursor = view['keys'], view['cursor']
        view['top'] = min(view['top'], cursor)
        view['top'] = max(view['top'], cursor - rows + 1)
        out = []
        for i in range(view['top'], min(len(keys), view['top'] + rows)):
            line = [('class:selected', '● ') if keys[i] in selected else ('', '  '), *labels[keys[i]]]
            if i == cursor:
                line = [(f'{style} class:current', text) for style, text in line]
            out += [*line, ('', '\n')]
        return out or [('class:dim', '  no matches\n')]

    def info():
        parts = [f'{len(view["keys"])}/{len(order)}']
        if multi:
            parts += [f'{len(selected)} selected', 'tab select', 'ctrl-a all']
        parts += ['enter accept', 'esc cancel']
        return [('class:dim', '  ' + ' · '.join(parts))]

    kb = KeyBindings()

    @kb.add('up')
    @kb.add('c-p')
    def _(event): move(-1)

    @kb.add('down')
    @kb.add('c-n')
    def _(event): move(1)

    @kb.add('pageup')
    def _(event): move(-rows)

    @kb.add('pagedown')
    def _(event): move(rows)

    if multi:
        @kb.add('tab')
        def _(event):
            if view['keys']:
                selected.symmetric_difference_update({view['keys'][view['cursor']]})
                move(1)

        @kb.add('s-tab')
        def _(event):
            if view['keys']:
                selected.symmetric_difference_update({view['keys'][view['cursor']]})
                move(-1)

        @kb.add('c-a')
        def _(event):
            visible = set(view['keys'])
            if visible <= selected:
                selected.difference_update(visible)
            else:
                selected.update(visible)

    @kb.add('enter')
    def _(event):
        if selected:
            event.app.exit(result=[k for k in order if k in selected])
        else:
            event.app.exit(result=view['keys'][view['cursor']:view['cursor'] + 1])

    @kb.add('escape', eager=True)
    @kb.add('c-c')
    def _(event):
        event.app.exit(result=None)

    prompt = Window(BufferControl(buffer, input_processors=[BeforeInput('> ', style='class:prompt')]), height=1)
    body = [prompt, Window(FormattedTextControl(lines), height=rows), Window(FormattedTextControl(info), height=1)]
    if message:
        body.insert(0, Window(FormattedTextControl([('class:message', message)]), height=1))
    app = Application(layout=Layout(HSplit(body), focused_element=prompt), key_bindings=kb, style=STYLE,
                      erase_when_done=True, input=input, output=output)
    return app.run()
