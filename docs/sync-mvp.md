# Sync MVP

Tests only the sync part of the [v2 plan](v2-plan.md): `library.json` with timestamps and
tombstones, conflict-copy merging, ID-based filenames, the server worker and `status.json`.
Downloads are **fake**: the worker writes a small placeholder `.mp3` (a text file) instead of
running yt-dlp. A song whose title starts with `FAIL` fails, to test error reporting.

Needs Python 3.11+ and `rich` on every computer. yt-dlp is not needed.

## Setup

On every device, clone the repo on this branch and `pip install rich` (or `pip install -e .`
for the `ytmm-mvp` command). Then:

```sh
# server (always-on machine)
python -m ytmm.mvp init ~/Music/ytmm --role server --name homeserver
python -m ytmm.mvp worker

# each computer
python -m ytmm.mvp init ~/Music/ytmm --name laptop
```

`init` writes `~/.config/ytmm/config.toml` and creates `library/` and `music/songs/`, each with a
`.stignore` that hides temp files. In Syncthing, share:

| Folder ID      | Path                     | Server       | Computers       | Phone        |
|----------------|--------------------------|--------------|-----------------|--------------|
| `ytmm-library` | `~/Music/ytmm/library`   | Send & Receive | Send & Receive | –            |
| `ytmm-music`   | `~/Music/ytmm/music`     | Send Only    | Receive Only    | Receive Only |

## Commands (computers)

```sh
python -m ytmm.mvp add dQw4w9WgXcQ -t "Never Gonna Give You Up" -a "Rick Astley"
python -m ytmm.mvp add "https://youtu.be/kJQP7kiw5Fk" -t "Despacito" -a "Luis Fonsi, Daddy Yankee"
python -m ytmm.mvp ls [text]       # ✓ file is on this device · … server pending · ✗ failed · ? not synced yet
python -m ytmm.mvp ls -d           # also show tombstones
python -m ytmm.mvp edit dQw4w9WgXcQ -t "New title"   # server renames the file
python -m ytmm.mvp rm despacito    # tombstone; server moves the file to its trash
python -m ytmm.mvp status          # what the server last reported
```

## Things worth testing

- Add on the laptop, then watch the file show up on the other computer and the phone.
- Edit a title and check that the file is renamed everywhere.
- Conflicts: turn off Wi-Fi on two computers, edit different songs on each (and the same song
  on both), reconnect. Syncthing makes a `library.sync-conflict-*.json`. The next `ytmm.mvp`
  command on any device, or the worker, merges it: newest edit per song wins and deletions stick.
- Remove a song while a computer is offline. It must stay removed after that computer comes back.
- Safety: the worker refuses to trash more than `max_trash` (10) files in one pass and reports it
  in `status`. Trashed files go to `~/.local/share/ytmm/trash/` on the server and are purged
  after 30 days.

## Notes

- "Newest edit" uses each device's clock, so keep clocks in sync (NTP).
- The worker checks for changes every `poll_interval` seconds (5) and rewrites `status.json` at
  least every 5 minutes as a heartbeat. Failed downloads are retried on that heartbeat.
- To try several "devices" on one machine, point each one at its own config with
  `YTMM_CONFIG=/path/to/device.toml`.
