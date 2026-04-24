
## TouchPortal Youtube Music Desktop Plugin
- [TouchPortal YTMD Plugin](#touchportal-youtube-music-desktop-plugin)
  - [Description](#description)
  - [Actions / States / Events](#actionsstatesevents)
    - [Actions](#actions)
    - [States](#states)
    - [Events](#events)
  - [Installation Guide](#installation)
  - [Settings Overview](#settings)
  - [Info](#info)

## Description
This is an integration for [TouchPortal](https://www.touch-portal.com/) that allows you to control the [YouTube Music Desktop app (YTMD)](https://ytmdesktop.app) using the v2 Companion Server API.

> **Requires YTMD v2.0 or later** with the Companion Server enabled.

## Actions / States / Events
### Actions
  - **YT Music Playback Play/Pause** — Pause or resume the current song
  - **YT Music Playback Next/Previous** — Skip to the next or previous track
  - **YT Music Control Like/Dislike** — Like, dislike, or clear the rating on the current song
  - **YT Music Control Volume** — Increase or decrease volume by a fixed step
  - **YT Music Playback Seek** — Forward or rewind by 10 seconds
  - **YT Music Playback Repeat** — Cycle repeat mode: Off → All → One
  - **YT Music Add Current Track to Playlist** — Add the current song to a chosen playlist
  - **YT Music Set Seek** — Jump to a specific position in the song (0–100%)
  - **YT Music Set Volume** — Set volume to an exact value (0–100)
  - **YT Music Play Track** — Play a specific track from the current queue
  - **YT Music Playback Shuffle** — Toggle queue shuffle

### Events
  - **YT Music is Paused** — Fires when playback state changes (True/False)
  - **YT Music Song Like State** — Fires when the like status changes (INDIFFERENT / LIKE / DISLIKE)
  - **YT Music is Advertisement** — Fires when an ad starts or ends (True/False)
  - **YT Music Song Repeat State** — Fires when repeat mode changes (OFF / ONE / ALL)

### States
  - **YT Music Song Title** — Current track title
  - **YT Music Cover Art** — Current track album art (image)
  - **YT Music Song Author** — Current track artist
  - **YT Music Current Album** — Current track album name
  - **YT Music PlayerhasSong** — Whether a track is loaded (True/False)
  - **YT Music Play/Pause State** — Whether playback is paused (True/False)
  - **YT Music Current Volume** — Current volume (0–100)
  - **YT Music Song Length** — Track duration (MM:SS)
  - **YT Music Song Progress** — Playback position as a percentage (0–100)
  - **YT Music Queue** — Titles of tracks in the current queue
  - **YT Music Playlists** — Your YouTube Music playlist names
  - **YT Music Status** — Connection status (Open / Closed)

> **Note:** "Add to Library" is not supported by the YTMD v2 Companion API and has no effect.

## Installation

1. **Install YTMD v2** — Download from [ytmdesktop.app](https://ytmdesktop.app) (v2.0 or later required).

2. **Enable the Companion Server** — Open YTMD settings, go to **Integrations**, and enable **Companion Server**.

3. **Install the plugin** — Download the latest `.tpp` release and import it into TouchPortal via **Settings → Import Plugin**.

4. **Authorize the plugin** — On first run the plugin will automatically request authorization from YTMD. A prompt will appear in YTMD asking you to approve the connection — click **Authorize**. The token is saved and reused automatically on future starts.

5. **Configure the host** *(optional)* — If YTMD is running on a different PC, set the `IPv4 address` setting in the TouchPortal plugin settings to that machine's IP address.

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `IPv4 address` | `localhost` | IP address of the machine running YTMD. Use `localhost` if YTMD is on the same PC. |
| `Status` | *(read-only)* | Shows whether the plugin is currently connected to YTMD. |

## Info
If you have any issues or suggestions, feel free to open an issue on GitHub or send an email!
