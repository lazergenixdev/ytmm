"""
Fake downloader for testing sync without yt-dlp: writes a small placeholder
file instead of audio. A song whose title starts with "FAIL" fails, so the
failure path in status.json can be tested too.
"""
import os, time
from pathlib import Path


class DownloadError(Exception):
    pass


def download(id: str, song: dict, dst: Path, delay: float = 0.0) -> None:
    if song.get('title', '').startswith('FAIL'):
        raise DownloadError('simulated failure (title starts with FAIL)')
    time.sleep(delay)
    part = dst.with_name(f'.{dst.name}.part')  # ignored through .stignore
    part.write_text(
        f'ytmm placeholder\nid: {id}\ntitle: {song.get("title")}\n'
        f'artists: {", ".join(song.get("artists") or [])}\n',
        encoding='utf-8',
    )
    os.replace(part, dst)
