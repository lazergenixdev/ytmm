"""
status.json (written only by the server, shown by `ytmm status`):
{
  "server":      str,           device name of the server
  "seen":        float,         last time the worker wrote this file
  "version":     str,           ytmm version on the server
  "ytdlp":       str | null,    yt-dlp version
  "songs":       int,           live songs in the library
  "files":       int,           files in music/songs
  "downloading": str | null,    video id being downloaded right now
  "pending":     [id],          songs without a file yet
  "failed":      {id: {"error": str, "attempts": int, "retry": float}},
  "blocked":     {"count": int, "limit": int, "token": str | null, "reason": str, "files": [str]} | null,
                                set when a plan was refused (too many files to trash)
  "canary":      {"time": float, "ok": bool, "error": str | null, "ytdlp": str} | null,
  "inbox":       {"time": float, "ok": bool, "error": str | null, "imported": int} | null,
  "update":      {"time": float, "ok": bool} | null,
  "error":       str | null     why the last pass did nothing (e.g. library.json unreadable)
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
