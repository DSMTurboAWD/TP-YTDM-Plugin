"""Unit tests for the TouchPortal YTMD Plugin.

Tests verify that the plugin modules correctly delegate to the YTMD SDK and
push the right states/updates to Touch Portal.

External dependencies (TouchPortalAPI, ytmd_sdk) are mocked so these tests
run without a live YTMD instance or Touch Portal connection.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

# ── path setup ────────────────────────────────────────────────────────────────
_PLUGIN_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', 'TouchPortalYTMusic'))
_LIB_DIR = os.path.join(_PLUGIN_DIR, 'lib')
sys.path.insert(0, _LIB_DIR)
sys.path.insert(0, _PLUGIN_DIR)

# ── pre-mock external deps before any plugin import ──────────────────────────
# Both mocks must be in sys.modules before config/tp_client are first imported.
_mock_tp_module = MagicMock()
sys.modules['TouchPortalAPI'] = _mock_tp_module

_mock_ytmd_instance = MagicMock()
_mock_ytmd_module = MagicMock()
_mock_ytmd_module.YTMD.return_value = _mock_ytmd_instance
# Give Events real string values so socketio.Client.on() gets proper event name keys.
_mock_events = type('Events', (), {
    'connect':          'connect',
    'disconnect':       'disconnect',
    'connect_error':    'connect_error',
    'state_update':     'state-update',
    'playlist_created': 'playlist-created',
    'playlist_deleted': 'playlist-deleted',
})()
_mock_ytmd_module.Events = _mock_events
sys.modules['ytmd_sdk'] = _mock_ytmd_module

import config        # noqa: E402  reads settings.json, creates ytmd singleton

class TestEntryTp(unittest.TestCase):
    """Validate that entry.tp is valid JSON and contains the expected structure."""

    @classmethod
    def setUpClass(cls):
        entry_path = os.path.join(_PLUGIN_DIR, "entry.tp")
        with open(entry_path, "r", encoding="utf-8") as f:
            cls.entry = json.load(f)
        # Flatten all states from all categories for convenient lookup.
        cls.state_ids = {
            s["id"]
            for cat in cls.entry.get("categories", [])
            for s in cat.get("states", [])
        }
        cls.action_ids = {
            a["id"]
            for cat in cls.entry.get("categories", [])
            for a in cat.get("actions", [])
        }

    def test_entry_tp_is_valid_json(self):
        self.assertIsInstance(self.entry, dict)

    def test_required_top_level_keys_present(self):
        for key in ("sdk", "version", "id", "categories"):
            self.assertIn(key, self.entry, f"Missing top-level key: {key}")

    def test_plugin_id_is_correct(self):
        self.assertEqual(self.entry["id"], "YoutubeMusic")

    def test_version_matches_settings_json(self):
        """entry.tp version integer must match the app_version in settings.json."""
        parts = [int(x) for x in config.APP_VERSION.split(".")]
        while len(parts) < 3:
            parts.append(0)
        expected = parts[0] * 100 + parts[1] * 10 + parts[2]
        self.assertEqual(self.entry["version"], expected,
            f"entry.tp version {self.entry['version']} does not match "
            f"settings.json app_version {config.APP_VERSION} (expected {expected})")

if __name__ == '__main__':
    unittest.main(verbosity=2)


class TestTokenStorage(unittest.TestCase):
    """Token persistence lives in the plugin's auth module, not the SDK."""

    def setUp(self):
        # Reset shared state and mock before each test.
        import state as plugin_state
        plugin_state.auth_token = None
        _mock_ytmd_instance.reset_mock()
        _mock_ytmd_instance.token = None

    def _write_token_file(self, path, token):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(token)

    def test_load_token_returns_none_when_file_missing(self):
        import auth
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('config.TOKEN_FILE', os.path.join(tmpdir, 'auth_token.txt')):
                import importlib, auth as _auth
                importlib.reload(_auth)
                result = _auth.load_token()
        self.assertIsNone(result)

    def test_load_token_reads_file_and_registers_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            token_path = os.path.join(tmpdir, 'auth_token.txt')
            self._write_token_file(token_path, 'saved-token')
            with patch('config.TOKEN_FILE', token_path):
                import importlib, auth as _auth
                importlib.reload(_auth)
                result = _auth.load_token()
        self.assertEqual(result, 'saved-token')
        _mock_ytmd_instance.update_token.assert_called_with('saved-token')

    def test_save_token_writes_file(self):
        _mock_ytmd_instance.token = 'my-token'
        with tempfile.TemporaryDirectory() as tmpdir:
            token_path = os.path.join(tmpdir, 'auth_token.txt')
            with patch('config.TOKEN_FILE', token_path):
                import importlib, auth as _auth
                importlib.reload(_auth)
                _auth.save_token()
            with open(token_path, 'r') as f:
                content = f.read()
        self.assertEqual(content, 'my-token')

    def test_clear_token_calls_revoke_and_removes_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            token_path = os.path.join(tmpdir, 'auth_token.txt')
            self._write_token_file(token_path, 'stale-token')
            with patch('config.TOKEN_FILE', token_path):
                import importlib, auth as _auth
                importlib.reload(_auth)
                _auth.clear_token()
            self.assertFalse(os.path.exists(token_path))
        _mock_ytmd_instance.revoke_token.assert_called_once()

    def test_clear_token_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            token_path = os.path.join(tmpdir, 'nonexistent_token.txt')
            with patch('config.TOKEN_FILE', token_path):
                import importlib, auth as _auth
                importlib.reload(_auth)
                _auth.clear_token()   # file never existed — must not raise
                _auth.clear_token()   # second call also must not raise
