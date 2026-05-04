import os
from config import TOKEN_FILE, ytmd, log
from tp_client import TPClient
import state


def load_token():
    """
      Read the token file from disk and register it on the YTMD SDK instance.
      Returns the token string, or None if the file is absent or empty.
    """
    try:
        with open(TOKEN_FILE, 'r', encoding='utf-8') as f:
            token = f.read().strip()
        if token:
            ytmd.update_token(token)
            return token
    except FileNotFoundError:
        pass
    return None


def save_token():
    """
      Persist the current token to TOKEN_FILE. Creates the directory if needed.
    """
    token = ytmd.token
    if not token:
        raise ValueError("No token to save — authenticate first")
    os.makedirs(os.path.dirname(os.path.abspath(TOKEN_FILE)), exist_ok=True)
    with open(TOKEN_FILE, 'w', encoding='utf-8') as f:
        f.write(token)


def clear_token():
    """
      Clear the in-memory token on the SDK instance and delete the token file.
      Idempotent — safe to call when the file does not exist.
    """
    state.auth_token = None
    ytmd.revoke_token()
    try:
        os.remove(TOKEN_FILE)
    except OSError:
        pass


def authenticate():
    """
      Two-step YTMD auth: request a code, wait for user approval, exchange for token.
      Returns True on success, False on any failure.
    """
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
