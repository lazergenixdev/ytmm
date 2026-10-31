# ytmm - YouTube Music Manager

A music library you keep in sync across computers and an Android phone. The song list is one
file, `library.json`, shared with [Syncthing](https://syncthing.net). An always-on home server
notices changes to it, downloads the songs with yt-dlp, tags them, and syncs the files back to
every device.

```
 laptop / desktop                     home server                        phone
 ────────────────                     ───────────                        ─────
 ytmm add / edit / rm                 ytmm worker
   │ writes library.json                │ watches library.json
   └──── Syncthing ───────────────────▶ │ downloads + tags (yt-dlp)
                                        │ writes music/ and status.json
   ◀──── Syncthing ──────────────────── ┴ ──── Syncthing ────────────▶ plays music/
```

Computers need no download tools; only the server runs yt-dlp. Songs added to an "inbox"
playlist on YouTube Music show up on every device even when no computer is on.

## Install

On each computer (Python 3.11+, via [uv](https://docs.astral.sh/uv/)):

```sh
uv tool install git+https://github.com/lazergenixdev/ytmm
ytmm init ~/Music/ytmm
```

Then share `~/Music/ytmm/library` and `~/Music/ytmm/music` with the server in Syncthing. Setting
up the server is described in [docs/server-setup.md](docs/server-setup.md).

## Usage

```sh
ytmm add 'https://music.youtube.com/watch?v=YQiau5iKLHc' # a song (any YouTube link or video id)
ytmm add 'https://music.youtube.com/playlist?list=...'  # every song of a playlist or album
ytmm add daft punk one more time                        # search, then pick from the results
ytmm add                                                # the links in the clipboard

ytmm ls                     # everything; ✓ marks files already on this device
ytmm ls daft                # fuzzy search on title, artists and album (typos are fine)
ytmm ls --regex '^One'      # regular expressions are opt-in
ytmm edit sleepwalker       # fix title, artists, album or year (prompts, or use --title etc.)
ytmm rm daft punk           # several matches open a picker: type to filter, tab to select
ytmm restore                # bring removed songs back
ytmm tag gym <songs>        # tags become playlists (music/playlists/gym.m3u)
ytmm status                 # what the server is doing, and what failed
```

When a command matches several songs, it opens a picker instead of asking for a more precise
pattern. `--all` takes every match instead, and `ytmm <command> --help` lists all options.
Quote links: shells like zsh treat the `?` in them as a wildcard.

## How syncing works

- Songs are identified by their YouTube video id. Files are named `Artist - Title [id].mp3`, so
  editing a title just makes the server rename and retag the file.
- Every song records when and where it was last edited, and removed songs leave a tombstone.
  When two devices edit the library at the same time, Syncthing keeps a conflict copy, and the
  next `ytmm` command on any device merges it: the newest edit of each song wins, and removals
  stick.
- Only the server writes music files. Removed songs' files go to a trash on the server for 30
  days, and the server refuses to trash many files at once unless you `ytmm approve` it.

The design and its reasons are in [docs/v2-plan.md](docs/v2-plan.md).

## Moving from v0.2

v2 replaces `music.json` with `library.json`. On the server, run
`ytmm migrate path/to/music.json`: it imports the songs and playlists (as tags) and moves the
files v0.2 downloaded to their new names (see [server setup](docs/server-setup.md#moving-from-v02)).

## Development

```sh
uv venv && uv pip install -e '.[server,test]'
.venv/bin/python -m pytest
```
