__all__ = [
    'main',
    '__version__',
]

__version__ = '2.0.0'


# Imported lazily so `import ytmm` stays cheap
def main(argv=None):
    from .cli import main
    return main(argv)
