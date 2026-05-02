
## TouchPortal YouTube Music Desktop Plugin

- [Description](#description)
- [Requirements](#requirements)
- [Installation](#installation)
- [Authentication](#authentication)
- [Actions](#actions)
- [States](#states)
- [Events](#events)
- [Settings](#settings)
- [Architecture](#architecture)
- [Info](#info)

## Description

A [TouchPortal](https://www.touch-portal.com/) plugin that integrates with the
[YouTube Music Desktop App (YTMD)](https://ytmdesktop.app) v2 Companion Server API.
Control playback, manage volume, browse your queue, and react to player state changes
directly from your TouchPortal button board.

## Requirements

- [YouTube Music Desktop App](https://ytmdesktop.app) **v2.0 or later**
- [TouchPortal](https://www.touch-portal.com/) desktop app
- Companion Server enabled in YTMD (Settings → Integrations → Companion Server → Enable)

## Installation

1. Download the latest `.tpp` release from the [Releases page](../../releases).
2. Open TouchPortal → **Settings → Import Plugin** and select the `.tpp` file.
3. If prompted, trust the plugin and restart TouchPortal.
4. In YTMD: open **Settings → Integrations → Companion Server** and toggle it **On**.

## Authentication

On first launch (or after clearing the auth token) the plugin requests a short-lived
approval code from YTMD and waits for you to approve it:

1. A prompt appears in the YTMD application window asking you to **Approve** or **Deny**
   the connection request.
2. Click **Approve** within 30 seconds.
3. The plugin receives a persistent token and saves it to
   `%APPDATA%\tpytmdplugin\auth_token.txt`.

The `TokenPresent` state in TouchPortal reflects whether a valid token is on disk.
If the token is ever rejected (e.g. after reinstalling YTMD), simply delete
`auth_token.txt` and restart the plugin — the approval prompt will reappear.

## Actions

| Action | Description |
|--------|-------------|
| YT Music Playback Play/Pause | Play or pause the current track |
| YT Music Playback Next/Previous | Skip to the next or previous track |
| YT Music Control Like/Dislike | Like or dislike the current track |
| YT Music Control Volume | Step volume up or down |
| YT Music Mute/Unmute | Mute or unmute audio |
| YT Music Playback Seek | Seek forward or rewind by 10 seconds |
| YT Music Playback Repeat | Set repeat mode to OFF / ONE / ALL |
| YT Music Playback Shuffle | Shuffle the current queue |
| YT Music Add to Playlist | Add the current track to a playlist |
| YT Music Set Seek | Jump to an exact position (seconds) |
| YT Music Set Volume | Set volume to an exact percentage (0–100) |
| YT Music Play Track | Play a specific track index in the queue |
| YT Music Start Playlist | Start a playlist by name |
| YT Music Play URL | Play a YouTube Music URL |

> **Note:** *Add to Library* and *Skip Ad* are declared in the UI but are not yet
> supported by the YTMD v2 Companion API. They log a warning when triggered.

## States

| State | Description |
|-------|-------------|
| Song Title | Title of the currently playing track |
| Song Author | Artist name |
| Current Album | Album name |
| Cover Art | Album artwork (base64-encoded image) |
| Cover Art URI | Raw thumbnail URL — use with TouchPortal's **Set button icon from URI** action |
| Track Changed Tick | Alternates `0`/`1` on every track change — drives the **YT Music Track Changed** event |
| Has Song | `True` / `False` — whether a track is loaded |
| Is Paused | `True` when paused, `False` when playing or buffering |
| Current Volume | Volume level 0–100 |
| Song Length | Total track duration (`MM:SS`) |
| Current Position | Playback position (`MM:SS`) |
| Seek Bar Status | Playback position as a percentage (0–100) |
| Song Like State | `LIKE` / `DISLIKE` / `INDIFFERENT` |
| Repeat Type | `NONE` / `ALL` / `ONE` |
| Is Advertisement | `True` when an ad is playing |
| Previous Song Title | Title of the previous track in the queue |
| Previous Song Author | Artist of the previous track |
| Next Song Title | Title of the next track in the queue |
| Next Song Author | Artist of the next track |
| Token Present | `True` when a valid auth token is saved on disk |
| Connection Debug | Current connection status message |

## Events

| Event | Trigger |
|-------|---------|
| YT Music is Paused | Fires when the paused state changes (`True` / `False`) |
| YT Music Song Like States | Fires when the like rating changes (`LIKE` / `DISLIKE` / `INDIFFERENT`) |
| YT Music is Advertisement | Fires when an ad starts or ends (`True` / `False`) |
| YT Music Repeat States | Fires when the repeat mode changes (`ALL` / `ONE` / `NONE`) |
| YT Music Track Changed | Fires whenever the track changes (`0` / `1` alternating) |

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| IPv4 address | `localhost` | Hostname or IP of the machine running YTMD. Change this to control YTMD on another PC on your network. |
| Status | *(read-only)* | Displays the current connection state (connected, disconnected, authenticating, etc.) |

## Architecture

The plugin is built on the
[`ytmd-sdk`](https://pypi.org/project/ytmd-sdk/) Python package, which handles
all communication with the YTMD Companion Server API (auth handshake, REST commands,
real-time Socket.IO state updates).

```
TouchPortal ←──[TouchPortalAPI SDK]──→ Plugin (TPYTMD.py)
                                              │
                                        lib/socketio_client.py  ← manages connection lifecycle
                                        lib/ytmd_client.py      ← dispatches commands & state
                                        lib/auth.py             ← token management
                                              │
                                        ytmd-sdk (pip package)
                                              │
                                     YTMD Companion Server :9863
```

The auth token is persisted to `%APPDATA%\tpytmdplugin\auth_token.txt`.
Diagnostic logs are written to `log.txt` in the plugin installation directory.

## Info

For bug reports, feature requests, or questions, please open an
[Issue](../../issues) on GitHub.
