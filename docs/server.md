# Running a ytmm server

Install Python 3.11+, ffmpeg and Deno, then install `uv tool install '.[server]'`
from the checkout. Run `ytmm init ~/Music/ytmm --role server`. Share `library/` as
Send & Receive and `music/` as Send Only in Syncthing. All other devices receive
music; only computers also edit the library share. Share the contents of each
folder directly, rather than nesting a share inside another share.

`ytmm worker --once` applies one pass; `ytmm worker` polls continuously. There is
one worker per library per machine, protected by an OS lock. The server should be
the only device configured with role `server`. Downloads are sequential so failed
songs can be reported and retried without stopping other work.

## Config

The device config is `~/.config/ytmm/config.toml`. An example server config:

```toml
root = "/home/me/Music/ytmm"
role = "server"
device = "homeserver"
trash = "/home/me/.local/share/ytmm/trash"
poll_interval = 5.0
max_trash = 10

# Optional: public/unlisted YouTube Music inbox, checked every five minutes.
# inbox_playlist = "https://music.youtube.com/playlist?list=YOUR_PLAYLIST_ID"
inbox_interval = 300.0

# Optional: choose a known available video for a daily real download test.
# canary_id = "dQw4w9WgXcQ"
canary_interval = 86400.0
# Optional HTTP endpoint accepting a POST body, for example an ntfy topic.
# notify_url = "https://ntfy.sh/YOUR_PRIVATE_TOPIC"
```

Restart the worker after editing config. Relative paths resolve against the config
directory. The trash directory must be outside both shares and must not contain
the library root. `fake_delay` is retained only for the old simulation; it has no
effect on real downloads.

For the inbox, create an unlisted YouTube Music playlist and set `inbox_playlist`
or pass `--inbox URL` during server initialization. Add songs to it from the phone;
the server imports new IDs without login or API keys. Existing songs and tombstones
are preserved: deleting an inbox song in ytmm does not make it reappear on the next
poll. The worker does not alter the remote playlist. Playlist privacy must allow
unauthenticated reads.

Canary checks are disabled until you choose a video. They run once per configured
interval, downloading privately and deleting the test audio afterward. Failures
are written to `status.json`; when `notify_url` is configured they also POST a
notification. Notification failures are reported without stopping the worker.

## Linux user service

The examples in [deploy/systemd](../deploy/systemd/) assume `uv` and `ytmm` are in
`~/.local/bin`. Adjust the paths if installed elsewhere:

```sh
mkdir -p ~/.config/systemd/user
cp deploy/systemd/ytmm.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ytmm.service
journalctl --user -u ytmm.service -f
```

The service PATH includes `~/.deno/bin`, `~/.local/bin`, `/usr/local/bin` and standard
Linux paths for Deno and ffmpeg. Ensure they are present for the service user.
To keep the user service running after logout, the server administrator can enable
lingering with `loginctl enable-linger USERNAME`. Syncthing should also run without
an interactive login.

## Updates

YouTube changes can require a newer yt-dlp and its JavaScript challenge support.
`uv tool upgrade ytmm` refreshes the installed tool and its dependencies, including
the server extra used at installation. The optional example timer does this daily
and restarts a running worker:

```sh
cp deploy/systemd/ytmm-update.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ytmm-update.timer
```

An installation from a local checkout must keep that checkout at the same path;
update its source before upgrading if you also want application changes. Update
Deno, ffmpeg, and any separately installed PO-token provider through their own
installation tools. Providers are environment-specific and are not provisioned by
ytmm. yt-dlp discovers its installed plugins normally. Use the chosen provider's
documentation if your server needs one. The canary exposes download breakage in
`ytmm status`; it does not automatically modify your installation.

## Recovery and troubleshooting

`ytmm status` reports the last server heartbeat, yt-dlp version, in-flight and failed
downloads, blocked trash plans, inbox errors and canary results. Heartbeats and
failed downloads are revisited at least every five minutes. Offline clients read
the last synced report, so check its age. A fresh status alone does not mean a
failed song downloaded successfully.

Trash is outside Syncthing and is kept for 30 days from removal. Stop the worker,
restore the entry with `ytmm add VIDEO_ID --title 'Title' --restore`, and move the
corresponding trashed MP3 back to `music/songs/`. Restart the worker to normalize
its name and tags. Do not place arbitrary backups in the trash directory: its old
regular files are purged. Keep independent backups for longer retention.

For a refused trash plan, inspect the synced library and conflict copies before
raising `max_trash` in the server config. Malformed conflict copies are left intact
for repair; the worker stops reconciliation until they are resolved. An absent
library never counts as an empty library. Never remove tombstones to save space:
they prevent older offline edits from restoring removed songs.

If the server is unavailable for an extended time, install the server extra, Deno,
and ffmpeg on one computer, stop the original server worker, and temporarily switch
that computer's music share to Send & Receive. Run `ytmm sync --local` for one pass.
After syncing, restore Receive Only before restarting the normal server. This is
an explicit fallback, not a second ongoing writer.
