"""
status.json (written only by the server):
{
  "server":  str,          device name of the server
  "seen":    float,        last time the worker wrote this file
  "songs":   int,          live songs in the library
  "files":   int,          files in music/songs
  "pending": [id],         songs without a file yet
  "failed":  {id: error},
  "blocked": str | null    set when a plan was refused (too many files to trash)
}
"""
import json, os
from pathlib import Path


def read(path: Path) -> dict | None:
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write(path: Path, status: dict) -> None:
    tmp = path.with_name(f'.{path.name}.tmp')
    tmp.write_text(json.dumps(status, indent=2, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8')
    os.replace(tmp, path)
