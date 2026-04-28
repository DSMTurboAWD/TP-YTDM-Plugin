import base64
import threading
import auth
import state
import requests

from config import ytmd, log
from tp_client import TPClient

# API values: -1=Unknown → INDIFFERENT, 0=Dislike, 1=Indifferent (no rating), 2=Like
LIKE_MAP   = {-1: "INDIFFERENT", 0: "DISLIKE", 1: "INDIFFERENT", 2: "LIKE"}
REPEAT_MAP = {-1: "NONE",        0: "NONE",    1: "ALL",         2: "ONE"}

_last_video_id = None
_state_cache: dict = {}  # tracks last-sent value per state ID to avoid redundant updates

# ----- volume debounce -----
# The TP volume connector can fire many events during a drag.
# Debouncing prevents hitting the API rate limit (2 commands/sec).
_volume_timer   = None
_volume_pending = None

def format_seconds(secs):
    try:
        m, s = divmod(int(float(secs or 0)), 60)
        return f"{m:02d}:{s:02d}"
    except Exception:
        return "00:00"

def ytmd_command(command, data):
    """Send a command to YTMD. Maps command name strings to SDK methods."""
    COMMAND_MAP = {
        "play":           ytmd.play,
        "pause":          ytmd.pause,
        "next":           ytmd.next,
        "previous":       ytmd.previous,
        "toggleLike":     ytmd.toggle_like,
        "toggleDislike":  ytmd.toggle_dislike,
        "volumeUp":       ytmd.volume_up,
        "volumeDown":     ytmd.volume_down,
        "mute":           ytmd.mute,
        "unmute":         ytmd.unmute,
        "shuffle":        ytmd.shuffle,
    }
    try:
        if command == "setVolume":
            resp = ytmd.set_volume(data)
        elif command == "seekTo":
            resp = ytmd.seek_to(data)
        elif command == "repeatMode":
            resp = ytmd.repeatMode(data)
        elif command == "playQueueIndex":
            resp = ytmd.play_index(data)
        elif command == "changeVideo":
            resp = ytmd.change_video(
                video_id=data.get("videoId") if data else None,
                playlist_id=data.get("playlistId") if data else None,
            )
        elif command in COMMAND_MAP:
            resp = COMMAND_MAP[command]()
        else:
            log(f"Unknown command: {command}")
            return

        log(f"Command: {command} data={data} status={resp.status_code}")
        if resp.status_code == 401:
            log("Command rejected (401) — will re-authenticate on next reconnect")
            auth.clear_token()
    except Exception as e:
        log(f"Command error ({command}): {e}")

def is_token_valid():
    return ytmd.is_token_valid()

def get_state():
    """Fetch current YTMD state. Returns parsed dict or None on any error."""
    try:
        resp = ytmd.get_state()
        if resp.status_code == 200:
            return resp.json()
        log(f"GET /state returned HTTP {resp.status_code}")
        return None
    except Exception as e:
        log(f"GET /state error: {e}")
        return None

def push_tp_states(state_data):
    global _last_video_id

    player = state_data.get("player") or {}
    video  = state_data.get("video")  or {}
    queue  = player.get("queue") or {}
    items  = queue.get("items")  or []

    selected_idx = queue.get("selectedItemIndex") or 0
    if selected_idx < 0:
        selected_idx = 0

    state.current_video_progress = float(player.get("videoProgress") or 0)
    duration_secs = float(video.get("durationSeconds") or 0) if video else 0

    track_state = player.get("trackState", -1)
    has_song    = str(bool(video))
    is_paused   = str(track_state == 0)   # 0=Paused, 1=Playing, 2=Buffering; -1=Unknown
    seek_pct = 0
    if duration_secs > 0:
        seek_pct = int(round((state.current_video_progress / duration_secs) * 100))

    like_int   = video.get("likeStatus", -1) if video else -1
    like_str   = LIKE_MAP.get(like_int, "INDIFFERENT")
    repeat_int = queue.get("repeatMode", -1)
    repeat_str = REPEAT_MAP.get(repeat_int, "NONE")

    # Fetch cover art only when track changes, in a daemon thread so we don't
    # block the Socket.IO event callback while the HTTP request is in-flight.
    video_id = video.get("id") if video else None
    if video_id and video_id != _last_video_id:
        _last_video_id = video_id
        thumbnails = video.get("thumbnails") or []
        if thumbnails:
            url = thumbnails[-1]["url"]
            def _fetch_cover(u=url):
                try:
                    cover_data = base64.b64encode(
                        requests.get(u, timeout=5).content
                    ).decode('utf-8')
                    TPClient.stateUpdate(
                        "KillerBOSS.TouchPortal.Plugin.YTMD.States.Playercover", cover_data
                    )
                except Exception as ex:
                    log(f"Cover art error: {ex}")
            threading.Thread(target=_fetch_cover, daemon=True).start()

    # Build candidate update list — only include entries whose value changed.
    candidates = [
        ("KillerBOSS.TouchPortal.Plugin.YTMD.States.PlayerTitle",
         str(video.get("title") or "") if video else ""),
        ("KillerBOSS.TouchPortal.Plugin.YTMD.States.Trackauthor",
         str(video.get("author") or "") if video else ""),
        ("KillerBOSS.TouchPortal.Plugin.YTMD.States.Trackalbum",
         str(video.get("album") or "") if video else ""),
        ("KillerBOSS.TouchPortal.Plugin.YTMD.States.PlayerhasSong",
         has_song),
        ("KillerBOSS.TouchPortal.Plugin.YTMD.States.PlayerisPaused",
         is_paused),
        ("KillerBOSS.TouchPortal.Plugin.YTMD.States.PlayerVPercent",
         str(int(player.get("volume") or 0))),
        ("KillerBOSS.TouchPortal.Plugin.YTMD.States.Trackdurationhuman",
         format_seconds(duration_secs)),
        ("KillerBOSS.TouchPortal.Plugin.YTMD.States.Trackcurrentdurationhuman",
         format_seconds(state.current_video_progress)),
        ("KillerBOSS.TouchPortal.Plugin.YTMD.States.PlayerCurrentSonglikeState",
         like_str),
        ("KillerBOSS.TouchPortal.Plugin.YTMD.States.isAdvertisement",
         str(player.get("adPlaying", False))),
        ("KillerBOSS.TouchPortal.Plugin.YTMD.States.SeekBarStatus",
         str(seek_pct)),
        ("KillerBOSS.TouchPortal.Plugin.YTMD.States.repeatType",
         repeat_str),
    ]

    # Always include previous/next so stale values are cleared when moving to queue edges.
    try:
        prev = items[selected_idx - 1] if selected_idx > 0 else None
    except (IndexError, KeyError):
        prev = None
    candidates += [
        ("KillerBOSS.TouchPortal.Plugin.YTMD.States.PreviousSong.title",
         str(prev.get("title") or "") if prev else ""),
        ("KillerBOSS.TouchPortal.Plugin.YTMD.States.PreviousSong.author",
         str(prev.get("author") or "") if prev else ""),
    ]

    try:
        nxt = items[selected_idx + 1] if selected_idx + 1 < len(items) else None
    except (IndexError, KeyError):
        nxt = None
    candidates += [
        ("KillerBOSS.TouchPortal.Plugin.YTMD.States.Next.title",
         str(nxt.get("title") or "") if nxt else ""),
        ("KillerBOSS.TouchPortal.Plugin.YTMD.States.Next.author",
         str(nxt.get("author") or "") if nxt else ""),
    ]

    updates = [
        {"id": sid, "value": val}
        for sid, val in candidates
        if _state_cache.get(sid) != val
    ]
    for entry in updates:
        _state_cache[entry["id"]] = entry["value"]

    try:
        if updates:
            TPClient.stateUpdateMany(updates)

        vol = int(player.get("volume") or 0)
        if _state_cache.get("_connector_volume") != vol:
            _state_cache["_connector_volume"] = vol
            TPClient.connectorUpdate(
                "KillerBOSS.TP.Plugins.YTMD.connectors.APPcontrol", vol
            )
    except Exception as e:
        log(f"State update error: {e}")

def refresh_playlists():
    """
        Fetch playlist list from YTMD and push choices to TP.
        Note: /api/v1/playlists has a rate limit of ~1 request per 30 seconds.
    """
    try:
        resp = ytmd.get_playlists()
        if resp.status_code == 200:
            playlists = resp.json()
            state.playlist_id_map = {p["title"]: p["id"] for p in playlists}
            TPClient.choiceUpdate(
                "KillerBOSS.TouchPortal.Plugin.YTMD.Action.AddToPlaylist.Value",
                list(state.playlist_id_map.keys())
            )
        else:
            log(f"Playlist fetch failed: HTTP {resp.status_code}")
    except Exception as e:
        log(f"Playlist refresh error: {e}")

def seed_initial_state():
    data = get_state()
    if data:
        push_tp_states(data)

def _flush_volume():
    global _volume_timer, _volume_pending
    _volume_timer = None
    if _volume_pending is not None:
        ytmd_command("setVolume", _volume_pending)
        _volume_pending = None

def debounced_set_volume(value):
    global _volume_timer, _volume_pending
    _volume_pending = value
    if _volume_timer:
        _volume_timer.cancel()
    _volume_timer = threading.Timer(0.3, _flush_volume)
    _volume_timer.start()
