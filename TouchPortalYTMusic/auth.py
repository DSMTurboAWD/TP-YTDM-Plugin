import json
import os
import urllib3

from config import APP_ID, APP_NAME, APP_VERSION, TOKEN_FILE, http, log
from tp_client import TPClient
import state


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
    state.auth_token = None
    try:
        os.remove(TOKEN_FILE)
    except OSError:
        pass


def authenticate():
    """Two-step YTMD auth: request a code, wait for user approval, exchange for token.
    Returns True on success, False on any failure."""
    try:
        body = json.dumps({
            "appId": APP_ID, "appName": APP_NAME, "appVersion": APP_VERSION
        }).encode()
        resp = http.request(
            "POST", f"http://{state.YTMD_server}:9863/api/v1/auth/requestcode",
            headers={"Content-Type": "application/json"}, body=body
        )
        if resp.status != 200:
            log(f"Auth requestcode failed: HTTP {resp.status}")
            TPClient.settingUpdate("status", "Auth failed — is YTMD running?")
            return False

        code = json.loads(resp.data)["code"]
        TPClient.settingUpdate("status", "Authenticating — approve in YTMD app (30s)…")
        log(f"Auth code received: {code}")

        body = json.dumps({"appId": APP_ID, "code": code}).encode()
        resp = http.request(
            "POST", f"http://{state.YTMD_server}:9863/api/v1/auth/request",
            headers={"Content-Type": "application/json"}, body=body,
            timeout=urllib3.Timeout(connect=5, read=35)
        )
        if resp.status != 200:
            log(f"Auth request failed: HTTP {resp.status}")
            TPClient.settingUpdate("status", "Auth denied or timed out")
            return False

        token = json.loads(resp.data).get("token")
        if not token:
            TPClient.settingUpdate("status", "Auth denied or timed out")
            return False

        state.auth_token = token
        save_token(token)
        log("Authentication successful, token saved")
        return True

    except Exception as e:
        log(f"Auth error: {e}")
        TPClient.settingUpdate("status", "Auth error — check log.txt")
        return False
