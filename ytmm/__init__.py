__all__ = [
    'main',
    'YoutubeMM',
]

# Imported lazily so the sync modules work on devices without yt-dlp
def __getattr__(name):
    if name == 'main':
        from .cli import main
        return main
    if name == 'YoutubeMM':
        from .ytmm import YoutubeMM
        return YoutubeMM
    raise AttributeError(name)
