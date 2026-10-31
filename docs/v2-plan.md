# ytmm v2 Plan

> **Note:** The existing `v2` branch is an incomplete earlier attempt. It is skipped;
> this plan starts from `main` (v0.2.x).

## Goals
Fix the two main pain points of v0.2:
1. **Keeping music in sync across devices** (multiple computers + an Android phone).
2. **User interface**: finding/selecting songs is hard (regex flags), and adding songs is tedious.

The master song list stays a **synced file**.

## Key decision: a home server does all downloading
yt-dlp breaks whenever YouTube changes, and keeping it working now needs a JS runtime (Deno)
and PO-token plugins as well. Instead of installing and updating that on every computer, one
always-on **home server** joins the Syncthing share as another device and is the only machine
that runs yt-dlp.

There is no public API. **Syncthing is the interface**: computers edit `library.json`, and the
server notices the change, downloads what is missing, and writes the files back into the share.

```
 laptop / desktop                     home server                        phone
 ────────────────                     ───────────                        ─────
 ytmm add / edit / rm                 ytmm worker
   │ writes library.json                │ watches library.json
   └──── Syncthing ───────────────────▶ │ downloads + tags (yt-dlp)
                                        │ writes music/ and status.json
   ◀──── Syncthing ──────────────────── ┴ ──── Syncthing ────────────▶ plays music/
```

What this gets us:
- yt-dlp, Deno, the PO-token provider and ffmpeg are only on the server. Computers need no
  download tooling.
- **Only the server writes music files.** Different devices can't race to download or rename
  the same song.
- Downloads happen even when no computer is on, so songs added to the inbox playlist from the
  phone just show up.
- A home IP address is blocked much less by YouTube than a VPS.

# 1. Sync layout

Two Syncthing folders, so that the "only the server writes music files" rule is enforced by
Syncthing itself, not just by convention:

```
~/Music/ytmm/
├── library/                 <- Syncthing folder "ytmm-library": Send & Receive on all computers + server
│   ├── library.json         <- the song list (edited by computers; server writes inbox imports + merges)
│   └── status.json          <- written only by the server: pending, failed, last canary check
└── music/                   <- Syncthing folder "ytmm-music": Send Only on server, Receive Only elsewhere
    ├── songs/
    │   └── Artist - Title [dQw4w9WgXcQ].mp3
    └── playlists/*.m3u      <- generated, so Android players see playlists
```

The phone only needs `ytmm-music`. Computers need both (`music/` for local playback).

Changes from v0.2 required for this to work:

| Problem in v0.2 | v2 |
|---|---|
| `root` is stored in `music.json` (a machine-specific path in a shared file) | Per-device config at `~/.config/ytmm/config.toml` holds the library path and the device role (`client` or `server`). Nothing device-specific is in the shared file. |
| Database is found by searching the current directory | Always loaded from the configured path; `ytmm` works from any directory |
| Filenames come only from the title, so renames and collisions break matching | Filenames include the video ID (`... [id].mp3`); files are matched by ID. Editing a title makes the server rename and retag the file. |
| Editing the list on two machines produces Syncthing conflict copies | Each entry gets an `updated` timestamp, and deletions leave a marker (tombstone) so merges can't resurrect them. Any device running ytmm (usually the server, since it is always on) merges `library.sync-conflict-*.json`, newest edit winning per song. The merge is deterministic, so it doesn't matter which device does it. |
| Metadata only lives in `music.json` | `library.json` is the source of truth for metadata; the server writes proper ID3 tags and cover art into each file so Android players display them correctly |

# 2. Adding songs

Adding only edits `library.json`. Metadata comes from
[`ytmusicapi`](https://github.com/sigma67/ytmusicapi), which handles search, playlists and
albums reliably without yt-dlp. It also returns cleaner artist / album / track fields than
yt-dlp.

- `ytmm add <playlist URL>`: add every song in a playlist or album at once.
- `ytmm add "artist song name"`: search YouTube Music and pick from the results.
- `ytmm add` with no arguments: read URLs from the clipboard.
- **Inbox playlist (adding from the phone):** an unlisted YouTube Music playlist (e.g.
  "ytmm inbox") whose URL is saved in the server's config. On the phone, just "Add to
  playlist". The server checks it on a timer and imports new songs into `library.json`. No
  login or API keys are needed for unlisted playlists.

# 3. Finding and selecting songs

- `ytmm ls daft`: fuzzy match on title, artist and album together, case-insensitive by
  default. Regex becomes opt-in (`--regex`).
- When a command (`rm`, `edit`, `tag`) matches several songs, open an interactive picker with
  type-to-filter and multi-select instead of requiring a precise pattern.
- `ytmm edit <song>`: fix title / artists / album / year (the override currently commented out
  in `cli.py`).
- Tags (`ytmm tag gym <songs>`) generate the `.m3u` playlists, useful on the phone.

# 4. The server worker

`ytmm worker` runs on the server (systemd service or Docker container). In a loop it:

1. Waits for `library.json` to change (a debounced file watch or polling the modification time),
   and checks the inbox playlist on a timer.
2. Merges any conflict copies.
3. Computes a plan (pure, in `plan.py`) by comparing the library with the files on disk:
   download, rename/retag, trash, regenerate playlists.
4. Applies it, then writes `status.json`.

Safety rules:
- Downloads, renames and retags are safe and happen automatically.
- Files are removed from `music/` only for songs with an explicit tombstone, or files that match
  no library entry. They are moved to a trash folder **outside** the synced folder on the server
  (e.g. `~/.local/share/ytmm/trash/`) and purged after 30 days. Other devices see a normal
  deletion, but the server can always restore the file.
- A bad merge can never wipe the library: the worker refuses to apply a plan that would trash
  more than a set limit (e.g. 10 songs or 10%) and records that in `status.json` instead.

On computers, `ytmm status` reads `status.json` and shows something like:

```
:: server last seen 2 min ago · yt-dlp 2026.10.22 · canary OK
:: 3 downloading, 1 failed (Video unavailable: dQw4w9WgXcQ), 0 pending trash
```

`ytmm status` replaces v0.2's per-file prompts in `sync`. Computers no longer download
anything.

## Keeping the server working
- **Updates:** a daily timer runs `uv tool upgrade yt-dlp`, or uses the nightly channel. The
  Deno runtime and PO-token provider are updated the same way (or as Docker images).
- **Canary:** once a day, download one known video. If it fails, put the failure in
  `status.json` and send a push notification (e.g. ntfy), so breakage is found before someone
  tries to add a song.
- **Fallback:** `ytmm sync --local` on a computer with yt-dlp installed runs the same plan
  locally, for when the server is down for a long time. It needs `music/` switched to Send &
  Receive on that computer while it is in use.

# Interface
Start with an improved CLI plus interactive pickers. A full-screen TUI (e.g. Textual) can be
built later on the same core if still wanted. A web app is not needed: the inbox playlist covers
adding from the phone, which was the only case that needed a UI outside a computer.

# Structure
```
ytmm/
  library.py   # data model, load/save, conflict merge (pure, easy to test)
  config.py    # per-device config, role = client | server
  plan.py      # library + files on disk -> actions (pure)
  sources.py   # ytmusicapi: metadata, search, playlists, clipboard, inbox
  status.py    # read/write status.json
  cli.py       # client commands and pickers
  worker.py    # server loop: watch, merge, plan, apply, canary
  download.py  # yt-dlp wrapper, tagging (server only)
```

Packaging: `uv tool install ytmm` on computers (no yt-dlp). `uv tool install 'ytmm[server]'`
on the server pulls in yt-dlp and mutagen; Deno (and ffmpeg, if MP3 is kept) are installed
separately or come with the Docker image.

# Milestones
1. New library format and device config; `ytmm migrate` converts an existing `music.json` and
   renames existing files to ID-based names (run on the server).
2. `ytmm worker`: ID-based filenames, downloads, ID3 tags, trash, `status.json`; `ytmm status`
   on computers.
3. Server operations: setup guide (Syncthing folders, systemd/Docker), daily updates, canary
   and notifications.
4. Conflict-copy merging (needed early: both computers and the server now write
   `library.json`).
5. Fuzzy `ls`, the picker, `edit`, `rm`.
6. New ways to add: playlists, search, clipboard, inbox.
7. Optional: tags and M3U playlists, `sync --local` fallback, then a TUI.

# Open questions
- **Which machine is the server?** It needs to be always on, have room for the whole library,
  and ideally run Linux (systemd/Docker).
- **Whole library on the phone, or a subset?** A subset means ignore patterns on the phone's
  copy of `ytmm-music`, populated from a tag such as `phone`.
- **Keep MP3?** On Android, Opus/M4A sounds better at the same size, but MP3 works everywhere.
  Saving native M4A means no ffmpeg on the server and no transcoding loss; MP3 is the only
  reason ffmpeg is required.
- **How fast should changes show up?** File watching gives seconds, polling gives minutes.
  Polling is simpler and Syncthing already adds some delay.

# Status (2.0.0)
Milestones 1–6 are done, and from milestone 7 the tags, M3U playlists and `sync --local`.
Setup and operations are in [server-setup.md](server-setup.md).

How the open questions were settled in code:
- **MP3:** still the default; `format = "m4a"` (no ffmpeg, no transcoding) or `"opus"` applies
  to new downloads. Files are matched by ID, so a library can mix formats.
- **Speed:** polling, but of the modification time every 5 seconds, which costs a few `stat`
  calls; the inbox is checked every 10 minutes.
- **Server machine:** still open. Docker and systemd setups are both provided.

Not done yet: the full-screen TUI, and syncing only a subset (e.g. a `phone` tag) to the phone.

Decisions made along the way:
- yt-dlp runs as a subprocess, so the daily `update_command` upgrades it without restarting the
  worker. The canary runs right after the update.
- The server keeps its bookkeeping (which tags each file has, download retries, inbox and
  canary state) in `~/.local/share/ytmm/state.json`, outside the synced folders.
- Tombstones keep the song's metadata, so `ytmm restore` can bring a song back; the server then
  takes the file out of its trash instead of downloading it again.
- A song newly appearing in the inbox playlist is imported once; deleting it from the library
  sticks even though it stays in the playlist (unauthenticated playlist reads have no
  per-item IDs, so "re-added" means it left the playlist and came back).
- The trash limit can be overridden with `ytmm approve`, which writes `library/approve.json`
  for the plan the server refused, so even that goes through Syncthing.
