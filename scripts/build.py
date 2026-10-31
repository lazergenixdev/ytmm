import subprocess
import os
import PyInstaller.__main__
from clean import clean

def build():
    clean()

    PyInstaller.__main__.run([
        'ytmm/__main__.py',
        '--onefile',
        '--name=ytmm',
        '--distpath=dist',
        '--workpath=.build',
        '--specpath=.build',
        '--optimize=2',
        '--collect-all=ytmusicapi',
        '--collect-submodules=yt_dlp',
        '--collect-submodules=prompt_toolkit',
    ])

if __name__ == '__main__':
    build()
