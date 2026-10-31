"""Shared worker status, written atomically and readable without server tooling."""
import json
import os
import tempfile
from pathlib import Path


def read(path: Path) -> dict | None:
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent, delete=False) as f:
            name = f.name
            json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)
