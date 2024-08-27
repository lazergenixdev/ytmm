from re import compile as regex

def __cprint(color, msg, **kwargs):
    print(f"{color}{msg}\033[0m", **kwargs)

def status(*values, **kwargs):
    print('', *values, **kwargs)

def error(*values, **kwargs):
    __cprint('\033[31m', " error", end=' ')
    print(':', *values, **kwargs)

re_log = regex(r'^\[.*\] ')

def yt_dlp_status(msg):
    print(' -->', re_log.sub('', msg))

def section(msg, **kwargs):
    __cprint('\033[36m', "::", end=' ')
    print(msg, **kwargs)

def ask(msg):
    __cprint('\033[36m', "::", end=' ')
    return input(f'{msg} [Y/n] ').lower() in ['yes', 'y']

def file(s: str):
    return f'\033[92m"{s}"\033[0m'

def special(s: str):
    return f'\033[93m"{s}"\033[0m'


""" ========= Concurrent Printing ========= """

_relative_pos = 0

def move_cursor_down(n): print(f'\033[{n}B', end='')
def move_cursor_up(n):   print(f'\033[{n}A', end='')
def move_cursor_beg():   print('\033[0G', end='')
def clear_line():        print('\033[2K', end='')

# TODO: fix when printing at bottom of terminal (scrolling)
def concurrent(task_index: int, msg):
    global _relative_pos
    if task_index > _relative_pos:
        move_cursor_down(task_index - _relative_pos)
    elif task_index < _relative_pos:
        move_cursor_up(_relative_pos - task_index)
    move_cursor_beg()
    clear_line()
    _relative_pos = task_index
    print(msg, end='', flush=True)

