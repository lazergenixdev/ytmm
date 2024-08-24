import subprocess
import sys
import os

def run_command(command):
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error running command: {' '.join(command)}")
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        sys.exit(result.returncode)
    return result

def build():
    # Define paths
    script_dir = os.path.dirname(os.path.abspath(__file__))
    build_dir = os.path.join(script_dir, 'build')
    dist_dir = os.path.join(script_dir, 'dist')

    # Clean up previous builds
    if os.path.exists(build_dir):
        run_command(['python', 'scripts/clean.py'])

    # Run PyInstaller
    run_command([
        'pyinstaller',
        '--onefile',
        '--distpath', dist_dir,
        '--workpath', build_dir,
        '--specpath', build_dir,
        'ytmm/cli.py'
    ])

if __name__ == '__main__':
    build()

