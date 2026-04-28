import os
import sys
import threading
from sys import exit

# Ensure lib/ is on the path for both source runs and the PyInstaller bundle.
_lib_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

from TouchPortalAPI import TYPES

from config import log, ytmd
from tp_client import TPClient
import state
from ytmd_client import (ytmd_command, debounced_set_volume, get_state, push_tp_states)
from socketio_client import sio, startup_sequence


@TPClient.on(TYPES.onConnect)
def onConnect(data: dict):
    state.running = True
    state.YTMD_server = data['settings'][0]['IPv4 address']
    # Normalize "localhost" to IPv4 — YTMD only listens on IPv4; on Windows
    # "localhost" often resolves to ::1 (IPv6) which fails immediately.
    ytmd_host = "127.0.0.1" if state.YTMD_server.lower() == "localhost" else state.YTMD_server
    ytmd.update_endpoint(ytmd_host)
    log(f"Connecting to YTMD at {state.YTMD_server}:9863")
    threading.Thread(target=startup_sequence, daemon=True).start()


@TPClient.on(TYPES.onAction)
def Actions(data: dict):
    action_id    = data['actionId']
    action_value = data['data'][0]['value'] if data.get('data') else None

    # CheckConnection works even when Socket.IO is not connected —
    # it tests the REST API directly and is useful for live diagnostics.
    if action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.CheckConnection":
        st = get_state()
        if st:
            player  = st.get("player") or {}
            video   = st.get("video")  or {}
            title   = video.get("title", "—") if video else "—"
            track   = player.get("trackState", "?")
            vol     = player.get("volume", "?")
            summary = f"OK | track={track} vol={vol} | {title}"
            log(f"CheckConnection: {summary}")
            TPClient.stateUpdate(
                "KillerBOSS.TouchPortal.Plugin.YTMD.States.ConnectionDebug", summary
            )
            push_tp_states(st)
        else:
            msg = "GET /state failed — check log.txt"
            log(f"CheckConnection: {msg}")
            TPClient.stateUpdate(
                "KillerBOSS.TouchPortal.Plugin.YTMD.States.ConnectionDebug", msg
            )
        return

    if not state.isYTMDRunning:
        return

    if action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.Play/Pause":
        ytmd_command("play" if action_value == "Play" else "pause")

    elif action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.Next/Previous":
        ytmd_command("next" if action_value == "Next" else "previous")

    elif action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.Like/Dislike":
        ytmd_command("toggleLike" if action_value == "Like" else "toggleDislike")

    elif action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.VUp/VDown":
        ytmd_command("volumeUp" if action_value == "Up" else "volumeDown")

    elif action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.forward/rewind":
        if action_value == "Forward":
            ytmd_command("seekTo", state.current_video_progress + 10)
        elif action_value == "Rewind":
            ytmd_command("seekTo", max(0.0, state.current_video_progress - 10))

    elif action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.RepeatPic":
        mode = {"ONE": 2, "All": 1, "OFF": 0}.get(action_value)
        if mode is not None:
            ytmd_command("repeatMode", mode)

    elif action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.mute/unmute":
        ytmd_command("mute" if action_value == "Mute" else "unmute")

    elif action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.AddToPlaylist":
        playlist_id = state.playlist_id_map.get(action_value)
        if playlist_id:
            ytmd_command("changeVideo", {"videoId": None, "playlistId": playlist_id})
        else:
            log(f"AddToPlaylist: unknown playlist '{action_value}'")

    elif action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.SetSeekBar":
        ytmd_command("seekTo", float(action_value))

    elif action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.SetVolume":
        ytmd_command("setVolume", int(action_value))

    elif action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.PlayTrackNumber":
        ytmd_command("playQueueIndex", int(action_value))

    elif action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.AddToLibrary":
        log("AddToLibrary is not supported in YTMD v2 API")

    elif action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.Shuffle":
        ytmd_command("shuffle")

    elif action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.StartPlaylist":
        ytmd_command("changeVideo", {"videoId": None, "playlistId": action_value})

    elif action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.PlayURL":
        video_id = action_value
        if "watch?v=" in action_value:
            video_id = action_value.split("watch?v=")[-1].split("&")[0]
        ytmd_command("changeVideo", {"videoId": video_id, "playlistId": None})

    elif action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.SkipAd":
        log("SkipAd is not supported in YTMD v2 API")


@TPClient.on(TYPES.onConnectorChange)
def connectorManager(data: dict):
    if data['connectorId'] == "KillerBOSS.TP.Plugins.YTMD.connectors.APPcontrol" and state.isYTMDRunning:
        debounced_set_volume(data['value'])


@TPClient.on(TYPES.onShutdown)
def Disconnect(data: dict):
    state.running = False
    try:
        sio.disconnect()
    except Exception:
        pass
    try:
        TPClient.disconnect()
    except (ConnectionResetError, AttributeError):
        pass
    log("Shutting Down")
    exit(0)


TPClient.connect()
