import traceback
import socketio
import auth
import state

from time import sleep
from config import ytmd, log, TOKEN_FILE
from tp_client import TPClient

from ytmd_client import (
    push_tp_states, seed_initial_state, refresh_playlists, is_token_valid
)
from ytmd_sdk import Events

# auto-reconnect is disabled; startup_sequence owns all reconnection logic
# so re-authentication can be interleaved cleanly when a token is rejected.
sio = socketio.Client(reconnection=False)

# YTMD's Socket.IO server uses the default /socket.io path.
# /api/v1/realtime is a Socket.IO namespace (not the server path).
_NS = "/api/v1/realtime"

@sio.on(Events.connect, namespace=_NS)
def on_sio_connect():
    state.isYTMDRunning = True
    log("Socket.IO connected")
    TPClient.settingUpdate("status", "YTMD is Open")
    seed_initial_state()
    refresh_playlists()

@sio.on(Events.disconnect, namespace=_NS)
def on_sio_disconnect():
    state.isYTMDRunning = False
    log("Socket.IO disconnected")
    TPClient.settingUpdate("status", "YTMD is Not open")

@sio.on(Events.connect_error, namespace=_NS)
def on_sio_connect_error(data):
    log(f"Socket.IO connect_error event: {data}")

@sio.on(Events.state_update, namespace=_NS)
def on_state_update(payload):
    push_tp_states(payload)

@sio.on(Events.playlist_created, namespace=_NS)
def on_playlist_created(playlist):
    refresh_playlists()

@sio.on(Events.playlist_deleted, namespace=_NS)
def on_playlist_deleted(playlist_id):
    refresh_playlists()

def startup_sequence():
    log(f"startup_sequence: thread started (running={state.running})")
    try:
        _startup_loop()
    except Exception as e:
        log(f"FATAL: startup_sequence crashed: {type(e).__name__}: {e}")
        log(traceback.format_exc())


def _startup_loop():
    # Resolve host once; used for both auth HTTP calls and Socket.IO connect.
    sio_host = "127.0.0.1" if state.YTMD_server.lower() == "localhost" else state.YTMD_server

    # Point the SDK at the correct host before any network call (auth or otherwise).
    ytmd.update_endpoint(sio_host)
    log(f"startup_sequence: YTMD endpoint → {ytmd.url}")

    log(f"startup_sequence: loading token from {TOKEN_FILE}")
    state.auth_token = auth.load_token()
    log(f"startup_sequence: token {'found' if state.auth_token else 'not found'}")

    # Report token-file presence to TouchPortal immediately so it's visible in the UI.
    TPClient.stateUpdate(
        "KillerBOSS.TouchPortal.Plugin.YTMD.States.TokenPresent",
        str(state.auth_token is not None)
    )

    # If a token was loaded from disk, verify YTMD still accepts it.
    # A stale token (e.g. from a prior install) would cause auth to be silently
    # skipped and the Socket.IO connect to fail without ever showing the popup.
    if state.auth_token and not ytmd.is_token_valid():
        log("startup_sequence: loaded token rejected by YTMD — clearing and re-authenticating")
        auth.clear_token()
        TPClient.stateUpdate(
            "KillerBOSS.TouchPortal.Plugin.YTMD.States.TokenPresent", "False"
        )

    while state.running:
        if not state.auth_token:
            log("No token — starting authentication flow…")
            TPClient.settingUpdate("status", "Requesting auth from YTMD…")
            if not auth.authenticate():
                sleep(30)  # respect YTMD rate limit on requestcode
                continue

        # Use ytmd.token as the single source of truth for the auth token.
        token = ytmd.token

        log(f"Socket.IO connecting → http://{sio_host}:9863/api/v1/realtime …")
        TPClient.settingUpdate("status", "Connecting to YTMD…")

        try:
            # Ensure we're fully disconnected before attempting a new connection.
            try:
                sio.disconnect()
            except Exception:
                pass

            sio.connect(
                f"http://{sio_host}:9863",
                # socketio_path defaults to "socket.io" — correct for YTMD
                transports=["websocket"],  # YTMD only supports WebSocket, not polling
                auth={"token": token},
                namespaces=[_NS],
                wait_timeout=10
            )
            sio.wait()  # blocks until the server disconnects or we call sio.disconnect()

        except socketio.exceptions.ConnectionError as e:
            log(f"Socket.IO connection failed: {e}")
            log(f"Traceback:\n{traceback.format_exc()}")
            valid = is_token_valid()
            log(f"Token validity check (GET /state): {'OK' if valid else 'INVALID — clearing'}")
            if not valid:
                auth.clear_token()
            else:
                TPClient.settingUpdate("status", "YTMD unreachable — retrying…")

        except Exception as e:
            log(f"Socket.IO unexpected error ({type(e).__name__}): {e}")
            log(f"Traceback:\n{traceback.format_exc()}")
            TPClient.settingUpdate("status", "Socket.IO error — retrying…")

        if state.running:
            sleep(5)
