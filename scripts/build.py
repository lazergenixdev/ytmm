import subprocess
import sys
import os

def build():
    # Clean up previous builds
    if os.path.exists('build'):
        subprocess.run(['python', 'scripts/clean.py'], check=True)

    # Run PyInstaller
    subprocess.run([
        'pyinstaller',
        '--onefile',
        '--distpath', 'dist',
        '--workpath', 'build',
        '--specpath', 'build',
        'ytmm/cli.py'
    ], check=True)

if __name__ == '__main__':
    build()

