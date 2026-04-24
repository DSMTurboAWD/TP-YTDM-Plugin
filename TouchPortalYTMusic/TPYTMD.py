import base64
import json
import os
import sys
import threading
from time import sleep, strftime
import socketio
import TouchPortalAPI
import urllib3
from TouchPortalAPI import TYPES
import requests
from sys import exit

# Resolve paths relative to the executable when running as a PyInstaller bundle,
# or relative to the script file when running from source.
if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

APP_ID      = "tpytmdplugin"
APP_NAME    = "TouchPortal YTMD Plugin"
APP_VERSION = "2.3.1"

# Store the token in %APPDATA%\tpytmdplugin\ so it survives plugin reinstalls.
# Log stays next to the executable for easy access.
_TOKEN_DIR = os.path.join(os.environ.get("APPDATA", _BASE_DIR), "tpytmdplugin")
os.makedirs(_TOKEN_DIR, exist_ok=True)
TOKEN_FILE = os.path.join(_TOKEN_DIR, "auth_token.txt")
LOG_FILE   = os.path.join(_BASE_DIR, "log.txt")

YTMD_server = "localhost"
auth_token  = None
isYTMDRunning = False
running = False
playlist_id_map = {}      # title -> id, populated from GET /api/v1/playlists
current_video_progress = 0.0  # seconds; updated on every state-update for seek offsets

http = urllib3.PoolManager(num_pools=10)

# ----- logging -----

def writeServerData(info):
    currenttime = strftime('[%I:%M:%S:%p] ')
    with open(LOG_FILE, 'a') as f:
        f.write(currenttime + str(info) + '\n')

# ----- helpers -----

def base_url():
    return f"http://{YTMD_server}:9863/api/v1"

def auth_headers():
    return {"Authorization": auth_token, "Content-Type": "application/json"}

def format_seconds(secs):
    try:
        m, s = divmod(int(float(secs or 0)), 60)
        return f"{m:02d}:{s:02d}"
    except Exception:
        return "00:00"

# v2 API enum mappings
LIKE_MAP   = {-1: "INDIFFERENT", 0: "DISLIKE", 1: "INDIFFERENT", 2: "LIKE"}
REPEAT_MAP = {-1: "NONE",        0: "NONE",    1: "ALL",         2: "ONE"}

# ----- auth token management -----

def load_token():
    try:
        with open(TOKEN_FILE, 'r') as f:
            token = f.read().strip()
            return token if token else None
    except FileNotFoundError:
        return None

def save_token(token):
    with open(TOKEN_FILE, 'w') as f:
        f.write(token)

def clear_token():
    global auth_token
    auth_token = None
    try:
        os.remove(TOKEN_FILE)
    except OSError:
        pass

def authenticate():
    """Request a code, wait for user approval in YTMD, exchange for token.
    Returns True on success, False on failure."""
    global auth_token
    try:
        body = json.dumps({
            "appId": APP_ID, "appName": APP_NAME, "appVersion": APP_VERSION
        }).encode()
        resp = http.request(
            "POST", f"http://{YTMD_server}:9863/api/v1/auth/requestcode",
            headers={"Content-Type": "application/json"}, body=body
        )
        if resp.status != 200:
            writeServerData(f"Auth requestcode failed: HTTP {resp.status}")
            TPClient.settingUpdate("status", "Auth failed — is YTMD running?")
            return False

        code = json.loads(resp.data)["code"]
        TPClient.settingUpdate("status", "Authenticating — approve in YTMD app (30s)…")
        writeServerData(f"Auth code received: {code}")

        body = json.dumps({"appId": APP_ID, "code": code}).encode()
        resp = http.request(
            "POST", f"http://{YTMD_server}:9863/api/v1/auth/request",
            headers={"Content-Type": "application/json"}, body=body,
            timeout=urllib3.Timeout(connect=5, read=35)
        )
        if resp.status != 200:
            writeServerData(f"Auth request failed: HTTP {resp.status}")
            TPClient.settingUpdate("status", "Auth denied or timed out")
            return False

        token = json.loads(resp.data).get("token")
        if not token:
            TPClient.settingUpdate("status", "Auth denied or timed out")
            return False

        auth_token = token
        save_token(token)
        writeServerData("Authentication successful, token saved")
        return True

    except Exception as e:
        writeServerData(f"Auth error: {e}")
        TPClient.settingUpdate("status", "Auth error — check log.txt")
        return False

# ----- YTMD command sender -----

def ytmd_command(command, data=None):
    body = {"command": command}
    if data is not None:
        body["data"] = data
    encoded = json.dumps(body).encode()
    try:
        resp = http.request(
            "POST", f"{base_url()}/command",
            headers=auth_headers(), body=encoded
        )
        writeServerData(f"Command: {command} data={data} status={resp.status}")
        if resp.status == 401:
            writeServerData("Command rejected (401) — will re-authenticate on next reconnect")
            clear_token()
    except Exception as e:
        writeServerData(f"Command error ({command}): {e}")

# ----- state mapping -----

last_video_id = None

def push_tp_states(state):
    global last_video_id, current_video_progress

    player = state.get("player") or {}
    video  = state.get("video")  or {}
    queue  = player.get("queue") or {}
    items  = queue.get("items")  or []

    selected_idx = queue.get("selectedItemIndex") or 0
    if selected_idx < 0:
        selected_idx = 0

    current_video_progress = float(player.get("videoProgress") or 0)
    duration_secs = float(video.get("durationSeconds") or 0) if video else 0

    track_state = player.get("trackState", -1)
    has_song    = str(bool(video))
    is_paused   = str(track_state == 0)   # 0=Paused, 1=Playing, 2=Buffering; -1=Unknown

    seek_pct = 0
    if duration_secs > 0:
        seek_pct = int(round((current_video_progress / duration_secs) * 100))

    like_int   = video.get("likeStatus", -1) if video else -1
    like_str   = LIKE_MAP.get(like_int, "INDIFFERENT")
    repeat_int = queue.get("repeatMode", -1)
    repeat_str = REPEAT_MAP.get(repeat_int, "NONE")

    # Fetch cover art only when track changes; done in a daemon thread to avoid
    # blocking the Socket.IO event callback while the HTTP request is in-flight.
    video_id = video.get("id") if video else None
    if video_id and video_id != last_video_id:
        last_video_id = video_id
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
                    writeServerData(f"Cover art error: {ex}")
            threading.Thread(target=_fetch_cover, daemon=True).start()

    updates = [
        {"id": "KillerBOSS.TouchPortal.Plugin.YTMD.States.PlayerTitle",
         "value": str(video.get("title") or "") if video else ""},
        {"id": "KillerBOSS.TouchPortal.Plugin.YTMD.States.Trackauthor",
         "value": str(video.get("author") or "") if video else ""},
        {"id": "KillerBOSS.TouchPortal.Plugin.YTMD.States.Trackalbum",
         "value": str(video.get("album") or "") if video else ""},
        {"id": "KillerBOSS.TouchPortal.Plugin.YTMD.States.PlayerhasSong",
         "value": has_song},
        {"id": "KillerBOSS.TouchPortal.Plugin.YTMD.States.PlayerisPaused",
         "value": is_paused},
        {"id": "KillerBOSS.TouchPortal.Plugin.YTMD.States.PlayerVPercent",
         "value": str(int(player.get("volume") or 0))},
        {"id": "KillerBOSS.TouchPortal.Plugin.YTMD.States.Trackdurationhuman",
         "value": format_seconds(duration_secs)},
        {"id": "KillerBOSS.TouchPortal.Plugin.YTMD.States.Trackcurrentdurationhuman",
         "value": format_seconds(current_video_progress)},
        {"id": "KillerBOSS.TouchPortal.Plugin.YTMD.States.PlayerCurrentSonglikeState",
         "value": like_str},
        {"id": "KillerBOSS.TouchPortal.Plugin.YTMD.States.isAdvertisement",
         "value": str(player.get("adPlaying", False))},
        {"id": "KillerBOSS.TouchPortal.Plugin.YTMD.States.SeekBarStatus",
         "value": str(seek_pct)},
        {"id": "KillerBOSS.TouchPortal.Plugin.YTMD.States.repeatType",
         "value": repeat_str},
    ]

    try:
        if selected_idx > 0 and len(items) > selected_idx - 1:
            prev = items[selected_idx - 1]
            updates += [
                {"id": "KillerBOSS.TouchPortal.Plugin.YTMD.States.PreviousSong.title",
                 "value": str(prev.get("title") or "")},
                {"id": "KillerBOSS.TouchPortal.Plugin.YTMD.States.PreviousSong.author",
                 "value": str(prev.get("author") or "")},
            ]
    except (IndexError, KeyError):
        pass

    try:
        next_idx = selected_idx + 1
        if next_idx < len(items):
            nxt = items[next_idx]
            updates += [
                {"id": "KillerBOSS.TouchPortal.Plugin.YTMD.States.Next.title",
                 "value": str(nxt.get("title") or "Unknown")},
                {"id": "KillerBOSS.TouchPortal.Plugin.YTMD.States.Next.author",
                 "value": str(nxt.get("author") or "Unknown")},
            ]
    except (IndexError, KeyError):
        pass

    try:
        TPClient.stateUpdateMany(updates)
        TPClient.connectorUpdate(
            "KillerBOSS.TP.Plugins.YTMD.connectors.APPcontrol",
            int(player.get("volume") or 0)
        )
    except Exception as e:
        writeServerData(f"State update error: {e}")

# ----- playlist management -----

def refresh_playlists():
    """Fetch playlist list from YTMD and push choices to TP.
    Note: /api/v1/playlists has a rate limit of ~1 request per 30 seconds."""
    global playlist_id_map
    try:
        resp = http.request("GET", f"{base_url()}/playlists", headers=auth_headers())
        if resp.status == 200:
            playlists = json.loads(resp.data)
            playlist_id_map = {p["title"]: p["id"] for p in playlists}
            TPClient.choiceUpdate(
                "KillerBOSS.TouchPortal.Plugin.YTMD.Action.AddToPlaylist.Value",
                list(playlist_id_map.keys())
            )
        else:
            writeServerData(f"Playlist fetch failed: HTTP {resp.status}")
    except Exception as e:
        writeServerData(f"Playlist refresh error: {e}")

def seed_initial_state():
    try:
        resp = http.request("GET", f"{base_url()}/state", headers=auth_headers())
        if resp.status == 200:
            push_tp_states(json.loads(resp.data))
    except Exception as e:
        writeServerData(f"Initial state seed error: {e}")

# ----- Socket.IO client -----

# auto-reconnect is disabled; the startup_sequence loop owns reconnection
# so that re-authentication can be interleaved cleanly when a token is rejected.
sio = socketio.Client(reconnection=False)

@sio.on("connect")
def on_sio_connect():
    global isYTMDRunning
    isYTMDRunning = True
    writeServerData("Socket.IO connected")
    TPClient.settingUpdate("status", "YTMD is Open")
    seed_initial_state()
    refresh_playlists()

@sio.on("disconnect")
def on_sio_disconnect():
    global isYTMDRunning
    isYTMDRunning = False
    writeServerData("Socket.IO disconnected")
    TPClient.settingUpdate("status", "YTMD is Not open")

@sio.on("connect_error")
def on_sio_connect_error(data):
    writeServerData(f"Socket.IO connect error: {data}")

@sio.on("state-update")
def on_state_update(state):
    push_tp_states(state)

@sio.on("playlist-created")
def on_playlist_created(playlist):
    refresh_playlists()

@sio.on("playlist-deleted")
def on_playlist_deleted(playlist_id):
    refresh_playlists()

# ----- volume debounce -----
# The TP volume connector can fire many events during a drag.
# Debouncing prevents hitting the API rate limit (2 commands/sec).
_volume_timer  = None
_volume_pending = None

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

def is_token_valid():
    """Verify the stored token by making a lightweight REST call.
    Returns False only on a definitive 401 — network errors are treated as 'still valid'
    so we don't clear a good token just because YTMD is temporarily unreachable."""
    try:
        resp = http.request(
            "GET", f"{base_url()}/state",
            headers=auth_headers(),
            timeout=urllib3.Timeout(connect=3, read=5)
        )
        return resp.status != 401
    except Exception:
        return True  # network error, not an auth failure

# ----- startup / reconnect loop -----

def startup_sequence():
    global auth_token
    auth_token = load_token()

    while running:
        # Authenticate if no token stored
        if not auth_token:
            if not authenticate():
                sleep(30)  # respect rate limit; requestcode is rate limited aggressively
                continue

        # Attempt Socket.IO connection.
        # YTMD only listens on IPv4; on Windows 'localhost' often resolves to ::1 (IPv6)
        # which causes an immediate connection failure. Force 127.0.0.1 in that case.
        sio_host = "127.0.0.1" if YTMD_server.lower() == "localhost" else YTMD_server
        try:
            sio.connect(
                f"http://{sio_host}:9863",
                socketio_path="api/v1/realtime",  # no leading slash — engineio prepends /
                transports=["websocket"],
                auth={"token": auth_token},
                wait_timeout=10
            )
            sio.wait()  # blocks until disconnect
        except Exception as e:
            writeServerData(f"Socket.IO error: {e}")
            # Verify the token is actually rejected rather than trusting the error message.
            # Any generic connection error (wrong path, server down) would also land here.
            if not is_token_valid():
                writeServerData("Token confirmed invalid (401) — clearing for re-authentication")
                clear_token()
            else:
                TPClient.settingUpdate("status", "YTMD is Not open")

        if running:
            sleep(5)  # pause before reconnect attempt

# ----- TouchPortal event handlers -----

TPClient = TouchPortalAPI.Client("YoutubeMusic")

@TPClient.on(TYPES.onConnect)
def onConnect(data):
    global YTMD_server, running
    print(data)
    running = True
    YTMD_server = data['settings'][0]['IPv4 address']
    print(f"Connecting to YTMD at {YTMD_server}:9863")
    threading.Thread(target=startup_sequence, daemon=True).start()

@TPClient.on(TYPES.onAction)
def Actions(data):
    if not isYTMDRunning:
        return

    action_id    = data['actionId']
    action_value = data['data'][0]['value'] if data.get('data') else None

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
            ytmd_command("seekTo", current_video_progress + 10)
        elif action_value == "Rewind":
            ytmd_command("seekTo", max(0.0, current_video_progress - 10))

    elif action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.RepeatPic":
        # v2 API accepts the target mode directly (no cycling needed)
        mode = {"ONE": 2, "All": 1, "OFF": 0}.get(action_value)
        if mode is not None:
            ytmd_command("repeatMode", mode)

    elif action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.mute/unmute":
        ytmd_command("mute" if action_value == "Mute" else "unmute")

    elif action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.AddToPlaylist":
        playlist_id = playlist_id_map.get(action_value)
        if playlist_id:
            ytmd_command("changeVideo", {"videoId": None, "playlistId": playlist_id})
        else:
            writeServerData(f"AddToPlaylist: unknown playlist '{action_value}'")

    elif action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.SetSeekBar":
        ytmd_command("seekTo", float(action_value))

    elif action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.SetVolume":
        ytmd_command("setVolume", int(action_value))

    elif action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.PlayTrackNumber":
        ytmd_command("playQueueIndex", int(action_value))

    elif action_id == "KillerBOSS.TouchPortal.Plugin.YTMD.Action.AddToLibrary":
        writeServerData("AddToLibrary is not supported in YTMD v2 API")

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
        writeServerData("SkipAd is not supported in YTMD v2 API")

@TPClient.on(TYPES.onConnectorChange)
def connectorManager(data):
    if data['connectorId'] == "KillerBOSS.TP.Plugins.YTMD.connectors.APPcontrol" and isYTMDRunning:
        debounced_set_volume(data['value'])

@TPClient.on(TYPES.onShutdown)
def Disconnect(data):
    global running
    running = False
    try:
        sio.disconnect()
    except Exception:
        pass
    try:
        TPClient.disconnect()
    except (ConnectionResetError, AttributeError):
        pass
    print("Shutting Down")
    exit(0)


TPClient.connect()
