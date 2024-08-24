import shutil
import os

def clean():
    paths_to_remove = ['dist', 'build', 'ytmm.spec']
    
    for path in paths_to_remove:
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.isfile(path):
            os.remove(path)

if __name__ == '__main__':
    clean()

