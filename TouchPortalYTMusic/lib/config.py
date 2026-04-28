import json
import os
import sys
from time import strftime

from ytmd_sdk import YTMD

if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_settings_path = os.path.join(_BASE_DIR, "settings.json")
with open(_settings_path, "r", encoding="utf-8") as _f:
    _settings = json.load(_f)

APP_ID      = _settings["app_id"]
APP_NAME    = _settings["app_name"]
APP_VERSION = _settings["app_version"]

_TOKEN_DIR = os.path.join(os.environ.get("APPDATA", _BASE_DIR), APP_ID)
os.makedirs(_TOKEN_DIR, exist_ok=True)
TOKEN_FILE = os.path.join(_TOKEN_DIR, "auth_token.txt")
LOG_FILE   = os.path.join(_BASE_DIR, "log.txt")

ytmd = YTMD(APP_ID, APP_NAME, APP_VERSION)

def log(msg):
    ts = strftime('[%I:%M:%S:%p] ')
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(ts + str(msg) + '\n')
