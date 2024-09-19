from re import compile as regex

class color:
    def red(s):
        return f'\033[31m{s}\033[0m'
    def blue(s):
        return f'\033[36m{s}\033[0m'

def status(*values, **kwargs):
    print('', *values, **kwargs)

def error(*values, **kwargs):
    __cprint('\033[31m', " error", end=' ')
    print(':', *values, **kwargs)

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


""" ============ Color Printing =========== """

def __cprint(color, msg, **kwargs):
    print(f"{color}{msg}\033[0m", **kwargs)
