from config import TOKEN_FILE, ytmd, log
from tp_client import TPClient
import state


def load_token():
    return ytmd.load_token(TOKEN_FILE)


def save_token():
    ytmd.save_token(TOKEN_FILE)


def clear_token():
    state.auth_token = None
    ytmd.clear_token(TOKEN_FILE)


def authenticate():
    """Two-step YTMD auth: request a code, wait for user approval, exchange for token.
    Returns True on success, False on any failure."""
    try:
        log(f"Auth: requesting code from {ytmd.url}/auth/requestcode …")
        TPClient.settingUpdate("status", "Authenticating — approve in YTMD app (30s)…")
        token = ytmd.authenticate()
        if not token:
            log("Auth: ytmd.authenticate() returned empty token")
            TPClient.settingUpdate("status", "Auth denied or timed out")
            return False

        log("Authentication successful, token saved")
        state.auth_token = token
        save_token()
        TPClient.stateUpdate(
            "KillerBOSS.TouchPortal.Plugin.YTMD.States.TokenPresent", "True"
        )
        return True

    except Exception as e:
        log(f"Auth error ({type(e).__name__}): {e}")
        TPClient.settingUpdate("status", "Auth error — check log.txt")
        return False
