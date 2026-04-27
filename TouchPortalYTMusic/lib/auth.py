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
        resp = ytmd.request_code()
        if resp.status_code != 200:
            log(f"Auth requestcode failed: HTTP {resp.status_code}")
            TPClient.settingUpdate("status", "Auth failed — is YTMD running?")
            return False

        code = resp.json()["code"]
        TPClient.settingUpdate("status", "Authenticating — approve in YTMD app (30s)…")
        log(f"Auth code received: {code}")

        resp = ytmd.request_token(code)
        if resp.status_code != 200:
            log(f"Auth request failed: HTTP {resp.status_code}")
            TPClient.settingUpdate("status", "Auth denied or timed out")
            return False

        token = resp.json().get("token")
        if not token:
            TPClient.settingUpdate("status", "Auth denied or timed out")
            return False

        ytmd.update_token(token)
        state.auth_token = token
        save_token()
        log("Authentication successful, token saved")
        return True

    except Exception as e:
        log(f"Auth error: {e}")
        TPClient.settingUpdate("status", "Auth error — check log.txt")
        return False
