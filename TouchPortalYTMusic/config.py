import os
import sys
import urllib3
from time import strftime

if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

APP_ID      = "tpytmdplugin"
APP_NAME    = "TouchPortal YTMD Plugin"
APP_VERSION = "2.4.0"

_TOKEN_DIR = os.path.join(os.environ.get("APPDATA", _BASE_DIR), "tpytmdplugin")
os.makedirs(_TOKEN_DIR, exist_ok=True)
TOKEN_FILE = os.path.join(_TOKEN_DIR, "auth_token.txt")
LOG_FILE   = os.path.join(_BASE_DIR, "log.txt")

http = urllib3.PoolManager(num_pools=10)

def log(msg):
    ts = strftime('[%I:%M:%S:%p] ')
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(ts + str(msg) + '\n')
