import subprocess
import os
import PyInstaller.__main__
from clean import clean

def build():
    if os.path.exists('build'):
        clean() # Clean up previous builds

    PyInstaller.__main__.run([
        'ytmm/__main__.py',
        '--onefile',
        '--name=ytmm',
        '--distpath=dist',
        '--workpath=build',
        '--specpath=build',
        '--optimize=2',
    ])

if __name__ == '__main__':
    build()
