# Server setup

The server is the one always-on machine that downloads music. Computers and the phone only
sync files with it through Syncthing (see the [v2 plan](v2-plan.md) for why). It needs to be
on all the time, have room for the whole library, and ideally run Linux.

There are two ways to run it: [installed directly](#1-install) with a systemd service, or
[with Docker](#docker). Both need the same Syncthing folders and config.

## 1. Install

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh   # uv, to install ytmm
curl -fsSL https://deno.land/install.sh | sh       # Deno: yt-dlp needs a JavaScript runtime for YouTube
sudo apt install ffmpeg syncthing                  # ffmpeg converts to MP3 (not needed with format = "m4a")
uv tool install 'ytmm[server] @ git+https://github.com/lazergenixdev/ytmm'
```

The `server` extra adds yt-dlp and mutagen. Computers install plain `ytmm`, without them.

**PO tokens (optional).** If YouTube starts refusing downloads ("Sign in to confirm you're not
a bot"), run a PO token provider and add its yt-dlp plugin:

```sh
docker run -d --name bgutil --restart unless-stopped -p 4416:4416 brainicism/bgutil-ytdlp-pot-provider
uv tool install --force 'ytmm[server] @ git+https://github.com/lazergenixdev/ytmm' --with bgutil-ytdlp-pot-provider
```

## 2. Syncthing folders

```sh
ytmm init ~/Music/ytmm --role server --name homeserver
```

This writes `~/.config/ytmm/config.toml` and creates two folders, each with a `.stignore` that
hides temporary files. Share them in Syncthing with these IDs and modes:

| Folder ID      | Path on the server       | Server         | Computers      | Phone        |
|----------------|--------------------------|----------------|----------------|--------------|
| `ytmm-library` | `~/Music/ytmm/library`   | Send & Receive | Send & Receive | (not shared) |
| `ytmm-music`   | `~/Music/ytmm/music`     | Send Only      | Receive Only   | Receive Only |

On each computer, run `ytmm init ~/Music/ytmm` (role `client` is the default) and accept both
folders at that path. On the phone, accept only `ytmm-music` and point your music player at it:
songs are in `songs/` and one playlist per tag is in `playlists/`.

Because `ytmm-music` is Send Only on the server, Syncthing itself makes sure no other device
changes music files.

## 3. Check and configure

```sh
ytmm canary
```

This lists the versions of yt-dlp, ffmpeg, Deno and mutagen, then downloads a short test video.
If it works, the server can download. Then edit `~/.config/ytmm/config.toml`. `ytmm init`
leaves the most useful options there, commented out:

| Key | Default | Meaning |
|---|---|---|
| `inbox` | | Unlisted YouTube Music playlist to import songs from ([below](#adding-songs-from-the-phone)) |
| `inbox_interval` | `600` | Seconds between inbox checks |
| `notify` | | ntfy topic URL for push notifications ([below](#notifications)) |
| `update_command` | | Run daily before the canary, e.g. `"uv tool upgrade ytmm"` |
| `canary` | `"jNQXAC9IVRw"` | Video downloaded daily to check that downloading works |
| `format` | `"mp3"` | `mp3`, `m4a` (no conversion, no ffmpeg) or `opus`. Only affects new downloads |
| `quality` | `"5"` | yt-dlp `--audio-quality` for MP3 (0 is best, 10 is smallest) |
| `ytdlp_args` | `[]` | Extra yt-dlp options, e.g. `["--cookies", "/path/to/cookies.txt"]` |
| `ytdlp` | | Command that runs yt-dlp, if not the one installed with ytmm |
| `max_trash`, `max_trash_percent` | `10`, `10` | Refuse to trash more files than this in one go ([below](#safety)) |
| `poll_interval` | `5` | Seconds between checks for changes to `library.json` |
| `data` | `~/.local/share/ytmm` | Server state, trash and temporary files (never synced) |

Every key is listed with its current value by `ytmm config`.

## 4. Run the worker

```sh
mkdir -p ~/.config/systemd/user
cp deploy/ytmm-worker.service ~/.config/systemd/user/
systemctl --user enable --now ytmm-worker
sudo loginctl enable-linger "$USER"   # keep it running while you're logged out
journalctl --user -u ytmm-worker -f   # watch what it does
```

The service's `PATH` includes `~/.deno/bin` and `~/.local/bin`. If Deno or uv are installed
somewhere else, edit the `Environment=PATH=` line.

What the worker does, in a loop:

1. Every 5 seconds, checks whether `library.json` changed. Every 10 minutes, checks the inbox
   playlist.
2. Merges any `library.sync-conflict-*.json` copies Syncthing left: newest edit wins per song.
3. Compares the library with `music/songs/`, then trashes, renames, retags and downloads,
   newest songs first. Files are named `Artist - Title [video id].mp3` and get ID3 tags and
   cover art from the library.
4. Writes `music/playlists/<tag>.m3u` and `library/status.json`, which `ytmm status` shows on
   every computer.

Once a day it runs `update_command` (if set) and then the canary.

## Docker

`deploy/docker-compose.yml` runs Syncthing, the worker and a PO token provider. Write
`deploy/data/config/config.toml` first:

```toml
root = "/ytmm"
role = "server"
device = "homeserver"
data = "/state"
ytdlp_args = ["--extractor-args", "youtubepot-bgutilhttp:base_url=http://bgutil:4416"]
update_command = "uv pip install --upgrade 'yt-dlp[default]' bgutil-ytdlp-pot-provider"
```

Then, from the repository root:

```sh
mkdir -p deploy/data/ytmm/library deploy/data/ytmm/music/songs deploy/data/state
docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml exec ytmm ytmm canary
```

Open Syncthing at `http://<server>:8384` and share `/var/syncthing/ytmm/library` and
`/var/syncthing/ytmm/music` as in [step 2](#2-syncthing-folders). Both containers run as user
1000, so their files agree on ownership. `update_command` upgrades yt-dlp inside the
container; rebuilding the image picks up new versions of ytmm itself.

## Moving from v0.2

Run this on the server, after `ytmm init`:

```sh
ytmm migrate path/to/music.json --dry-run   # see what would happen
ytmm migrate path/to/music.json
```

It adds every song to `library.json` (keeping their order), turns playlists into tags, and
moves the files v0.2 downloaded into `music/songs/` with ID-based names, so they aren't
downloaded again. It finds the old files the way v0.2 did (`root` in music.json); pass
`--files DIR` if they're elsewhere. Files are matched by name, or by the video URL that v0.2's
yt-dlp saved in their tags, which also sorts out songs that shared a file name in v0.2. The
worker then retags every file with cover art and downloads whatever has no file.

## Adding songs from the phone

1. In YouTube Music, create a playlist (e.g. "ytmm inbox") and set it to **Unlisted**.
2. Copy its link into the server's config: `inbox = "https://music.youtube.com/playlist?list=..."`.
3. On the phone, use "Save to playlist" on any song.

The worker imports new songs within `inbox_interval` (10 minutes) and downloads them. Songs can
stay in the playlist: removing one from the library on a computer doesn't bring it back. To add
a removed song again, take it out of the inbox playlist, wait for a check, and add it back (or
use `ytmm restore` on a computer).

## Notifications

With `notify = "https://ntfy.sh/<topic>"`, the worker sends a push notification when:

- the daily canary fails (yt-dlp broke), and when it works again;
- `update_command` fails;
- it refuses to trash files ([below](#safety));
- `library.json` can't be read.

Install the [ntfy](https://ntfy.sh) app and subscribe to the same topic. Topics on ntfy.sh are
public, so pick a name nobody will guess, or use your own ntfy server.

## Keeping downloads working

YouTube changes often and yt-dlp follows within days. With
`update_command = "uv tool upgrade ytmm"`, the worker upgrades yt-dlp daily and then runs the
canary, so a breakage that an update doesn't fix gets reported before anyone adds a song.
yt-dlp runs as a separate process, so upgrades take effect without restarting the worker (new
versions of ytmm itself do need a restart: `systemctl --user restart ytmm-worker`). Deno can be
updated the same way: `update_command = "uv tool upgrade ytmm && deno upgrade"`.

Failed downloads are retried after 5 minutes, then less and less often, up to once a day.
Editing the song, or the canary working again after failing, retries immediately.
`ytmm status` shows every failure with yt-dlp's error.

## Safety

- Files are only removed from `music/` for songs removed from the library (`ytmm rm`), or for
  files that match no song. They go to the trash in the server's data folder, outside the synced
  folders, and are deleted after 30 days. Other devices just see the file disappear.
- `ytmm restore` on any computer brings a song back. The server takes its file out of the trash
  instead of downloading it again.
- If a pass would trash more than `max_trash` files (10) or `max_trash_percent` of the library
  (10%), whichever is larger, the worker refuses, notifies you, and shows it in `ytmm status`.
  If it's intended (you removed many songs), run `ytmm approve` on any computer. An unreadable
  or missing `library.json` never trashes anything.
- "Newest edit wins" uses each device's clock, so keep clocks synced (NTP, the default nearly
  everywhere).

## When the server is down

`ytmm sync --local` does the server's work on a computer that has `ytmm[server]`, ffmpeg and
Deno installed. Switch `ytmm-music` to **Send & Receive** in Syncthing on that computer first,
and back to Receive Only afterwards. When the server comes back, it downloads those songs
itself, because its folder is Send Only and doesn't accept files from other devices.

## Testing without downloading

To try syncing between devices without yt-dlp, set `downloader = "fake"` in the server's config:
the worker then writes small placeholder files instead of audio, and songs whose title starts
with `FAIL` fail. Several "devices" can run on one machine by giving each its own config:

```sh
YTMM_CONFIG=/tmp/server.toml ytmm init /tmp/server --role server --name server
YTMM_CONFIG=/tmp/laptop.toml ytmm init /tmp/laptop --name laptop
```

Use a separate `data = "..."` folder in each config.

## Troubleshooting

- **The canary fails.** Run `ytmm canary` to see the error. Try `uv tool upgrade ytmm` (new
  yt-dlp), check that Deno is on the service's `PATH`, then consider the PO token provider or
  `ytdlp_args = ["--cookies", "..."]`.
- **"The server stopped reporting" in `ytmm status`.** The worker isn't running
  (`systemctl --user status ytmm-worker`), or Syncthing isn't connected to the server.
- **A song doesn't download.** `ytmm status` shows the error. `ytmm ls --failed` lists every
  failed song.
- **`pip install .` or `uv tool install .` fails with "could not create 'build/lib/ytmm'".**
  A file named `build` is in the way; setuptools needs `build/` to be a folder.
