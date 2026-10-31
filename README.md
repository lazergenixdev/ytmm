# ytmm — YouTube Music Manager v2

Keep a shared music library on computers and an Android phone. Computers edit a
small JSON library; one home server downloads and tags the audio. Syncthing moves
the library and finished MP3s between devices. No public API or web service is needed.

Requires Python 3.11+. From this checkout, install on each computer:

```sh
uv tool install .
ytmm init ~/Music/ytmm --name laptop
```

On the server, install download dependencies as well:

```sh
uv tool install '.[server]'
ytmm init ~/Music/ytmm --role server --name homeserver
ytmm worker
```

The server also needs **ffmpeg** and **Deno** available on its PATH. Clients need
neither, and do not install yt-dlp or mutagen. `pip install .` and
`pip install '.[server]'` work too, preferably in a virtual environment.

Share these **two separate folders** in Syncthing:

| Folder | Server | Computers | Phone |
| --- | --- | --- | --- |
| `~/Music/ytmm/library` | Send & Receive | Send & Receive | Not needed |
| `~/Music/ytmm/music` | Send Only | Receive Only | Receive Only |

Device config lives at `~/.config/ytmm/config.toml`, so every command works from
any directory. `YTMM_CONFIG` selects a different config file.

## Commands

```sh
ytmm add 'https://music.youtube.com/watch?v=dQw4w9WgXcQ'
ytmm add 'https://music.youtube.com/playlist?list=PL...'
ytmm add 'artist song name'         # search and pick songs
ytmm add                            # URLs from the clipboard
ytmm ls daft                        # fuzzy, case-insensitive matching
ytmm ls 'daft.*punk' --regex         # explicit regular expression
ytmm edit dQw4w9WgXcQ --title 'New title' --artist 'Rick Astley' --year 1987
ytmm tag gym daft                    # server generates an M3U playlist
ytmm rm dQw4w9WgXcQ                 # tombstone; server trashes the audio
ytmm status                         # last server report, downloads, failures
ytmm worker --once                   # server: one reconciliation pass
```

Search covers title, artists, album and tags. When `edit`, `rm` or `tag` matches
several songs, type to filter the picker, use arrows to move, Tab to select,
Enter to apply, or Escape to cancel. Scripts can use video IDs or `--all` to
explicitly apply a command to every match. `query` is an alias for `ls`.

Adding edits metadata only. Existing entries and deleted songs are skipped,
preserving manual edits; `add --restore` explicitly restores a deletion. To add
a single video without a metadata request, supply `--title` (and optionally
`--artist`, `--album`, `--year`). `edit --album ''` or `--artist ''` clears those
fields; `--year 0` removes the year from the audio tag.

Albums and public/unlisted playlists import all available tracks. A watch URL
that includes `list=` imports the whole collection. Clipboard support uses
`pbpaste` on macOS, PowerShell on Windows, or `wl-paste`, `xclip` or `xsel` on Linux.

## Upgrade an existing library

Initialize the v2 server first, stop its worker, and run:

```sh
ytmm migrate /path/to/music.json --dry-run
ytmm migrate /path/to/music.json
ytmm worker --once
```

The original database stays intact. Migration preserves song metadata, moves
existing MP3s to ID-based filenames, and reports missing or ambiguous files.
Existing v2 entries and tombstones win. Interrupted migrations can be rerun;
destinations are never overwritten. Use `--music-dir /path/to/old/music` if the
old database's relative root is ambiguous. Back up your old audio before migrating.

## Server setup and safety

See [the server guide](docs/server.md) for inbox playlists, systemd, updates,
canary checks, trash recovery, and the explicit local fallback.

The worker polls every five seconds, merges Syncthing conflict copies, and matches
files by video ID. Title/artist edits rename the file; album/year/track/cover edits
retag it. Downloads and retags publish finished files atomically. Tag playlists
use relative paths so they work on Android.

Files are moved to server trash outside the shares, then purged after 30 days.
Each pass refuses to trash more than `max_trash` files (default 10). A missing or
invalid library, or unreadable conflict copy, blocks reconciliation and appears
in status. Keep device clocks synchronized: the newest timestamp wins per song,
with deterministic tie breaking and persistent deletion tombstones.

## Development

```sh
python -m pip install -e '.[server,testing]'
python -m unittest discover -s tests -p 'test*.py'
```

Tests run offline with injected downloaders and metadata clients. The old
`ytmm-mvp` / `python -m ytmm.mvp` entry point remains a **fake download simulation**;
its text placeholders are not playable audio. Use a separate root when trying it.
The legacy embedded `YoutubeMM` API remains available for old integrations and
requires the server extra; it still uses the old database format.

The [v2 design plan](docs/v2-plan.md) records the architecture and open decisions.
MP3 and polling remain the defaults. Phone subsets, a full-screen TUI, and
alternative audio formats are deferred.
