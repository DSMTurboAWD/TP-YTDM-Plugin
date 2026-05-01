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

# ── import plugin modules (order matters) ────────────────────────────────────
import config        # noqa: E402  reads settings.json, creates ytmd singleton
import tp_client     # noqa: E402  TPClient = TouchPortalAPI.Client(...)
import state         # noqa: E402  shared mutable state
import auth          # noqa: E402  auth flow delegates to config.ytmd
import ytmd_client   # noqa: E402  command dispatch + state push
import socketio_client  # noqa: E402  startup loop and Socket.IO lifecycle

# ── helpers ──────────────────────────────────────────────────────────────────

def _ok(status_code=200, json_data=None):
    """Return a mock Response-like object with a given status and JSON body."""
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_data if json_data is not None else {}
    return r


def _sample_state(
    track_state=1,
    volume=75,
    video_progress=60.0,
    title="Test Song",
    author="Test Artist",
    album="Test Album",
    duration=120,
    like_status=1,
    repeat_mode=0,
    video_id="vid123",
    selected_idx=1,
    items=None,
):
    """Build a minimal YTMD state dict suitable for push_tp_states()."""
    if items is None:
        items = [
            {"title": "Prev Song",    "author": "Prev Artist"},
            {"title": "Current Song", "author": "Current Artist"},
            {"title": "Next Song",    "author": "Next Artist"},
        ]
    return {
        "player": {
            "trackState":    track_state,
            "volume":        volume,
            "videoProgress": video_progress,
            "adPlaying":     False,
            "queue": {
                "selectedItemIndex": selected_idx,
                "repeatMode":        repeat_mode,
                "items":             items,
            },
        },
        "video": {
            "id":              video_id,
            "title":           title,
            "author":          author,
            "album":           album,
            "durationSeconds": duration,
            "likeStatus":      like_status,
            "thumbnails":      [],   # omit to skip cover-art thread
        },
    }


def _reset_all():
    """Full reset between tests: mock call history, state, and module caches."""
    _mock_ytmd_instance.reset_mock()
    # Explicitly clear side_effect on methods commonly set in tests.
    for method in (
        'authenticate', 'request_code', 'request_token', 'get_state',
        'get_playlists', 'is_token_valid', 'load_token',
    ):
        getattr(_mock_ytmd_instance, method).side_effect = None
    tp_client.TPClient.reset_mock()
    ytmd_client._state_cache.clear()
    ytmd_client._last_video_id = None
    state.auth_token = None
    state.playlist_id_map = {}


# ══════════════════════════════════════════════════════════════════════════════
# TestSettings — config.py reads app identity from settings.json
# ══════════════════════════════════════════════════════════════════════════════

class TestSettings(unittest.TestCase):

    def test_app_id_loaded(self):
        self.assertEqual(config.APP_ID, "tpytmdplugin")

    def test_app_name_loaded(self):
        self.assertIsInstance(config.APP_NAME, str)
        self.assertTrue(len(config.APP_NAME) > 0)

    def test_app_version_is_semver(self):
        parts = config.APP_VERSION.split(".")
        self.assertGreaterEqual(len(parts), 2, "APP_VERSION should be semver-ish")

    def test_ytmd_singleton_instantiated_with_app_identity(self):
        _mock_ytmd_module.YTMD.assert_called_with(
            config.APP_ID, config.APP_NAME, config.APP_VERSION)


# ══════════════════════════════════════════════════════════════════════════════
# TestAuth — auth.py delegates all steps to the SDK
# ══════════════════════════════════════════════════════════════════════════════

class TestAuth(unittest.TestCase):

    def setUp(self):
        _reset_all()

    # -- token helpers ---------------------------------------------------------

    def test_load_token_delegates_to_sdk(self):
        auth.load_token()
        _mock_ytmd_instance.load_token.assert_called_once_with(config.TOKEN_FILE)

    def test_save_token_delegates_to_sdk(self):
        auth.save_token()
        _mock_ytmd_instance.save_token.assert_called_once_with(config.TOKEN_FILE)

    def test_clear_token_delegates_to_sdk_and_clears_state(self):
        state.auth_token = "stale-token"
        auth.clear_token()
        _mock_ytmd_instance.clear_token.assert_called_once_with(config.TOKEN_FILE)
        self.assertIsNone(state.auth_token)

    # -- authenticate() happy path --------------------------------------------

    def test_authenticate_success_calls_sdk_authenticate(self):
        _mock_ytmd_instance.authenticate.return_value = "TOKEN-XYZ"

        result = auth.authenticate()

        self.assertTrue(result)
        _mock_ytmd_instance.authenticate.assert_called_once()
        _mock_ytmd_instance.save_token.assert_called_once_with(config.TOKEN_FILE)

    def test_authenticate_success_sets_state_auth_token(self):
        _mock_ytmd_instance.authenticate.return_value = "TOKEN-XYZ"

        auth.authenticate()

        self.assertEqual(state.auth_token, "TOKEN-XYZ")

    # -- authenticate() failure paths -----------------------------------------

    def test_authenticate_returns_false_on_none_token(self):
        _mock_ytmd_instance.authenticate.return_value = None

        result = auth.authenticate()

        self.assertFalse(result)
        _mock_ytmd_instance.save_token.assert_not_called()

    def test_authenticate_returns_false_on_empty_token(self):
        _mock_ytmd_instance.authenticate.return_value = ""

        result = auth.authenticate()

        self.assertFalse(result)
        _mock_ytmd_instance.save_token.assert_not_called()

    def test_authenticate_returns_false_on_network_exception(self):
        _mock_ytmd_instance.authenticate.side_effect = ConnectionError("refused")

        result = auth.authenticate()

        self.assertFalse(result)


# ══════════════════════════════════════════════════════════════════════════════
# TestYTMDCommands — ytmd_command() dispatches to the correct SDK method
# ══════════════════════════════════════════════════════════════════════════════

class TestYTMDCommands(unittest.TestCase):

    def setUp(self):
        _reset_all()
        # Give every SDK method a default 200 OK return value.
        for method in (
            'play', 'pause', 'next', 'previous',
            'toggle_like', 'toggle_dislike',
            'volume_up', 'volume_down', 'mute', 'unmute', 'shuffle',
            'set_volume', 'seek_to', 'repeatMode', 'play_index', 'change_video',
        ):
            getattr(_mock_ytmd_instance, method).return_value = _ok()

    # -- simple (no-data) commands --------------------------------------------

    _SIMPLE = [
        ("play",          "play"),
        ("pause",         "pause"),
        ("next",          "next"),
        ("previous",      "previous"),
        ("toggleLike",    "toggle_like"),
        ("toggleDislike", "toggle_dislike"),
        ("volumeUp",      "volume_up"),
        ("volumeDown",    "volume_down"),
        ("mute",          "mute"),
        ("unmute",        "unmute"),
        ("shuffle",       "shuffle"),
    ]

    def test_simple_commands_call_correct_sdk_method(self):
        for cmd, sdk_method in self._SIMPLE:
            with self.subTest(command=cmd):
                _mock_ytmd_instance.reset_mock()
                getattr(_mock_ytmd_instance, sdk_method).return_value = _ok()
                ytmd_client.ytmd_command(cmd, None)
                getattr(_mock_ytmd_instance, sdk_method).assert_called_once()

    # -- data commands --------------------------------------------------------

    def test_set_volume_calls_sdk(self):
        ytmd_client.ytmd_command("setVolume", 50)
        _mock_ytmd_instance.set_volume.assert_called_once_with(50)

    def test_set_volume_zero_is_not_dropped(self):
        """Volume=0 must not be silently swallowed."""
        ytmd_client.ytmd_command("setVolume", 0)
        _mock_ytmd_instance.set_volume.assert_called_once_with(0)

    def test_seek_to_calls_sdk(self):
        ytmd_client.ytmd_command("seekTo", 30)
        _mock_ytmd_instance.seek_to.assert_called_once_with(30)

    def test_seek_to_zero_is_not_dropped(self):
        ytmd_client.ytmd_command("seekTo", 0)
        _mock_ytmd_instance.seek_to.assert_called_once_with(0)

    def test_repeat_mode_calls_sdk(self):
        ytmd_client.ytmd_command("repeatMode", 1)
        _mock_ytmd_instance.repeatMode.assert_called_once_with(1)

    def test_play_queue_index_calls_sdk(self):
        ytmd_client.ytmd_command("playQueueIndex", 2)
        _mock_ytmd_instance.play_index.assert_called_once_with(2)

    def test_change_video_calls_sdk_with_both_ids(self):
        ytmd_client.ytmd_command("changeVideo", {"videoId": "abc123", "playlistId": "PL456"})
        _mock_ytmd_instance.change_video.assert_called_once_with(
            video_id="abc123", playlist_id="PL456")

    # -- 401 handling ---------------------------------------------------------

    def test_401_response_clears_auth_token(self):
        _mock_ytmd_instance.play.return_value = _ok(401)
        state.auth_token = "stale-token"

        ytmd_client.ytmd_command("play", None)

        self.assertIsNone(state.auth_token)
        _mock_ytmd_instance.clear_token.assert_called_once_with(config.TOKEN_FILE)

    # -- edge cases -----------------------------------------------------------

    def test_unknown_command_does_not_raise(self):
        ytmd_client.ytmd_command("nonExistentCommand", None)  # must not raise or call any method

    # -- helpers delegated through ytmd_client --------------------------------

    def test_is_token_valid_delegates_to_sdk(self):
        _mock_ytmd_instance.is_token_valid.return_value = True
        result = ytmd_client.is_token_valid()
        _mock_ytmd_instance.is_token_valid.assert_called_once()
        self.assertTrue(result)

    def test_get_state_returns_parsed_json_on_200(self):
        payload = {"player": {}, "video": None}
        _mock_ytmd_instance.get_state.return_value = _ok(200, payload)
        result = ytmd_client.get_state()
        _mock_ytmd_instance.get_state.assert_called_once()
        self.assertEqual(result, payload)

    def test_get_state_returns_none_on_non_200(self):
        _mock_ytmd_instance.get_state.return_value = _ok(401)
        self.assertIsNone(ytmd_client.get_state())

    def test_get_state_returns_none_on_exception(self):
        _mock_ytmd_instance.get_state.side_effect = ConnectionError("down")
        self.assertIsNone(ytmd_client.get_state())


# ══════════════════════════════════════════════════════════════════════════════
# TestRefreshPlaylists — playlist fetch updates TP choices
# ══════════════════════════════════════════════════════════════════════════════

class TestRefreshPlaylists(unittest.TestCase):

    def setUp(self):
        _reset_all()

    def test_success_calls_sdk_and_updates_tp_choices(self):
        playlists = [
            {"title": "Favorites", "id": "PL001"},
            {"title": "Workout",   "id": "PL002"},
        ]
        _mock_ytmd_instance.get_playlists.return_value = _ok(200, playlists)

        ytmd_client.refresh_playlists()

        _mock_ytmd_instance.get_playlists.assert_called_once()
        self.assertEqual(state.playlist_id_map, {"Favorites": "PL001", "Workout": "PL002"})
        tp_client.TPClient.choiceUpdate.assert_called_once_with(
            "KillerBOSS.TouchPortal.Plugin.YTMD.Action.AddToPlaylist.Value",
            ["Favorites", "Workout"])

    def test_non_200_does_not_update_choices(self):
        _mock_ytmd_instance.get_playlists.return_value = _ok(429)
        ytmd_client.refresh_playlists()
        tp_client.TPClient.choiceUpdate.assert_not_called()

    def test_network_exception_does_not_raise(self):
        _mock_ytmd_instance.get_playlists.side_effect = ConnectionError("timeout")
        ytmd_client.refresh_playlists()  # must not raise


# ══════════════════════════════════════════════════════════════════════════════
# TestPushTPStates — state mapping from YTMD payload to TP state updates
# ══════════════════════════════════════════════════════════════════════════════

class TestPushTPStates(unittest.TestCase):

    def setUp(self):
        _reset_all()

    # -- helpers --------------------------------------------------------------

    def _pushed(self):
        """Collect all values sent via stateUpdateMany as {state_id: value}."""
        result = {}
        for c in tp_client.TPClient.stateUpdateMany.call_args_list:
            for entry in c.args[0]:
                result[entry["id"]] = entry["value"]
        return result

    def _push(self, **kwargs):
        with patch('ytmd_client.threading.Thread'):  # prevent cover-art thread
            ytmd_client.push_tp_states(_sample_state(**kwargs))
        return self._pushed()

    # -- track state ----------------------------------------------------------

    def test_paused_track_state(self):
        states = self._push(track_state=0)
        self.assertEqual(states["KillerBOSS.TouchPortal.Plugin.YTMD.States.PlayerisPaused"], "True")

    def test_playing_track_state(self):
        states = self._push(track_state=1)
        self.assertEqual(states["KillerBOSS.TouchPortal.Plugin.YTMD.States.PlayerisPaused"], "False")

    # -- like status ----------------------------------------------------------

    def test_like_status_like(self):
        states = self._push(like_status=2)
        self.assertEqual(states["KillerBOSS.TouchPortal.Plugin.YTMD.States.PlayerCurrentSonglikeState"], "LIKE")

    def test_like_status_dislike(self):
        states = self._push(like_status=0)
        self.assertEqual(states["KillerBOSS.TouchPortal.Plugin.YTMD.States.PlayerCurrentSonglikeState"], "DISLIKE")

    def test_like_status_indifferent(self):
        states = self._push(like_status=1)
        self.assertEqual(states["KillerBOSS.TouchPortal.Plugin.YTMD.States.PlayerCurrentSonglikeState"], "INDIFFERENT")

    # -- repeat mode ----------------------------------------------------------

    def test_repeat_all(self):
        states = self._push(repeat_mode=1)
        self.assertEqual(states["KillerBOSS.TouchPortal.Plugin.YTMD.States.repeatType"], "ALL")

    def test_repeat_one(self):
        states = self._push(repeat_mode=2)
        self.assertEqual(states["KillerBOSS.TouchPortal.Plugin.YTMD.States.repeatType"], "ONE")

    def test_repeat_none(self):
        states = self._push(repeat_mode=0)
        self.assertEqual(states["KillerBOSS.TouchPortal.Plugin.YTMD.States.repeatType"], "NONE")

    # -- seek / duration ------------------------------------------------------

    def test_seek_percentage_calculated_correctly(self):
        # 60s progress out of 120s = 50%
        states = self._push(video_progress=60.0, duration=120)
        self.assertEqual(states["KillerBOSS.TouchPortal.Plugin.YTMD.States.SeekBarStatus"], "50")

    def test_duration_formatted_as_mm_ss(self):
        states = self._push(duration=90)
        self.assertEqual(states["KillerBOSS.TouchPortal.Plugin.YTMD.States.Trackdurationhuman"], "01:30")

    def test_progress_formatted_as_mm_ss(self):
        states = self._push(video_progress=65.0)
        self.assertEqual(states["KillerBOSS.TouchPortal.Plugin.YTMD.States.Trackcurrentdurationhuman"], "01:05")

    # -- song metadata --------------------------------------------------------

    def test_song_metadata_pushed(self):
        states = self._push(title="My Song", author="My Artist", album="My Album")
        self.assertEqual(states["KillerBOSS.TouchPortal.Plugin.YTMD.States.PlayerTitle"],   "My Song")
        self.assertEqual(states["KillerBOSS.TouchPortal.Plugin.YTMD.States.Trackauthor"],   "My Artist")
        self.assertEqual(states["KillerBOSS.TouchPortal.Plugin.YTMD.States.Trackalbum"],    "My Album")

    def test_has_song_true_when_video_present(self):
        states = self._push()
        self.assertEqual(states["KillerBOSS.TouchPortal.Plugin.YTMD.States.PlayerhasSong"], "True")

    # -- volume connector -----------------------------------------------------

    def test_volume_connector_updated(self):
        with patch('ytmd_client.threading.Thread'):
            ytmd_client.push_tp_states(_sample_state(volume=75))
        tp_client.TPClient.connectorUpdate.assert_called_with(
            "KillerBOSS.TP.Plugins.YTMD.connectors.APPcontrol", 75)

    # -- cover art ------------------------------------------------------------

    def test_cover_art_fetched_via_sdk_on_new_video(self):
        """When a new video_id appears with a thumbnail, fetch_cover_art is called."""
        fake_bytes = b'\x89PNG\r\n'
        _mock_ytmd_instance.fetch_cover_art.return_value = fake_bytes

        data = _sample_state(video_id="newvid")
        data["video"]["thumbnails"] = [{"url": "https://example.com/art.jpg"}]

        # Run synchronously by executing the thread target inline.
        captured_target = {}
        def _capture_thread(*args, **kwargs):
            captured_target['fn'] = kwargs.get('target') or args[0]
            m = MagicMock()
            m.start = lambda: captured_target['fn']()
            return m

        with patch('ytmd_client.threading.Thread', side_effect=_capture_thread):
            ytmd_client.push_tp_states(data)

        _mock_ytmd_instance.fetch_cover_art.assert_called_once_with(
            "https://example.com/art.jpg")

        import base64
        expected_b64 = base64.b64encode(fake_bytes).decode('utf-8')
        # stateUpdate is called twice: once for CoverArtURI (before thread), once for Playercover (inside thread).
        tp_client.TPClient.stateUpdate.assert_any_call(
            "KillerBOSS.TouchPortal.Plugin.YTMD.States.CoverArtURI", "https://example.com/art.jpg")
        tp_client.TPClient.stateUpdate.assert_any_call(
            "KillerBOSS.TouchPortal.Plugin.YTMD.States.Playercover", expected_b64)

    def test_cover_art_not_fetched_for_same_video(self):
        """fetch_cover_art must not fire again when video_id is unchanged."""
        _mock_ytmd_instance.fetch_cover_art.return_value = b'\x89PNG'
        data = _sample_state(video_id="samevid")
        data["video"]["thumbnails"] = [{"url": "https://example.com/art.jpg"}]

        with patch('ytmd_client.threading.Thread'):
            ytmd_client.push_tp_states(data)  # first call — fires
        _mock_ytmd_instance.reset_mock()
        tp_client.TPClient.reset_mock()

        with patch('ytmd_client.threading.Thread') as mock_thread:
            ytmd_client.push_tp_states(data)  # second call — same video_id
            mock_thread.assert_not_called()    # no new thread spawned

    # -- queue navigation -----------------------------------------------------

    def test_previous_and_next_populated_in_mid_queue(self):
        items = [
            {"title": "Prev Song", "author": "Prev Artist"},
            {"title": "Current",   "author": "Current Artist"},
            {"title": "Next Song", "author": "Next Artist"},
        ]
        # selected_idx=1 → prev=items[0], next=items[2]
        states = self._push(selected_idx=1, items=items)
        self.assertEqual(states["KillerBOSS.TouchPortal.Plugin.YTMD.States.PreviousSong.title"], "Prev Song")
        self.assertEqual(states["KillerBOSS.TouchPortal.Plugin.YTMD.States.Next.title"],         "Next Song")

    def test_previous_cleared_when_at_first_track(self):
        """Moving to first track: Previous states must be cleared, not left stale."""
        items = [
            {"title": "First Song", "author": "Artist A"},
            {"title": "Second Song", "author": "Artist B"},
        ]
        # First call at idx=1 so PreviousSong.title gets populated.
        self._push(selected_idx=1, items=items)
        tp_client.TPClient.reset_mock()

        # Second call at idx=0 — no previous exists.
        states2 = self._push(selected_idx=0, items=items, video_id="vid456")
        self.assertEqual(states2.get("KillerBOSS.TouchPortal.Plugin.YTMD.States.PreviousSong.title", ""), "")
        self.assertEqual(states2.get("KillerBOSS.TouchPortal.Plugin.YTMD.States.PreviousSong.author", ""), "")

    def test_next_cleared_when_at_last_track(self):
        """Moving to last track: Next states must be cleared, not left stale."""
        items = [
            {"title": "First Song",  "author": "Artist A"},
            {"title": "Second Song", "author": "Artist B"},
        ]
        # First call at idx=0 so Next.title gets populated.
        self._push(selected_idx=0, items=items)
        tp_client.TPClient.reset_mock()

        # Second call at idx=1 (last) — no next exists.
        states2 = self._push(selected_idx=1, items=items, video_id="vid456")
        self.assertEqual(states2.get("KillerBOSS.TouchPortal.Plugin.YTMD.States.Next.title", ""), "")
        self.assertEqual(states2.get("KillerBOSS.TouchPortal.Plugin.YTMD.States.Next.author", ""), "")

    # -- deduplication --------------------------------------------------------

    def test_duplicate_state_not_re_pushed(self):
        data = _sample_state()
        with patch('ytmd_client.threading.Thread'):
            ytmd_client.push_tp_states(data)
        tp_client.TPClient.reset_mock()

        with patch('ytmd_client.threading.Thread'):
            ytmd_client.push_tp_states(data)
        tp_client.TPClient.stateUpdateMany.assert_not_called()

    # -- no video -------------------------------------------------------------

    def test_no_video_clears_song_metadata(self):
        data = {
            "player": {
                "trackState": -1, "volume": 0, "videoProgress": 0,
                "adPlaying": False,
                "queue": {"selectedItemIndex": 0, "repeatMode": 0, "items": []},
            },
            "video": None,
        }
        with patch('ytmd_client.threading.Thread'):
            ytmd_client.push_tp_states(data)
        states = self._pushed()
        self.assertEqual(states["KillerBOSS.TouchPortal.Plugin.YTMD.States.PlayerhasSong"], "False")
        self.assertEqual(states["KillerBOSS.TouchPortal.Plugin.YTMD.States.PlayerTitle"],   "")


# ══════════════════════════════════════════════════════════════════════════════
# TestFormatSeconds — pure helper function
# ══════════════════════════════════════════════════════════════════════════════

class TestFormatSeconds(unittest.TestCase):

    def test_zero(self):
        self.assertEqual(ytmd_client.format_seconds(0), "00:00")

    def test_ninety_seconds(self):
        self.assertEqual(ytmd_client.format_seconds(90), "01:30")

    def test_over_one_hour(self):
        self.assertEqual(ytmd_client.format_seconds(3661), "61:01")

    def test_none_returns_zero(self):
        self.assertEqual(ytmd_client.format_seconds(None), "00:00")

    def test_string_number(self):
        self.assertEqual(ytmd_client.format_seconds("75"), "01:15")

    def test_float_truncated(self):
        self.assertEqual(ytmd_client.format_seconds(90.9), "01:30")


# ══════════════════════════════════════════════════════════════════════════════
# TestStartupLoopTokenValidation — _startup_loop() validates tokens on load
# ══════════════════════════════════════════════════════════════════════════════

class TestStartupLoopTokenValidation(unittest.TestCase):
    """Tests for the token-validation logic that runs before the main while loop
    in socketio_client._startup_loop().

    The key invariant: loading a stale token must not silently skip auth.
    A loaded token must be validated against YTMD before being trusted.
    """

    def setUp(self):
        _reset_all()
        state.YTMD_server = "127.0.0.1"
        # Prevent the while loop body from executing; tests only check pre-loop logic.
        state.running = False

    def test_valid_loaded_token_skips_clear_and_auth(self):
        """Token accepted by YTMD: neither clear_token() nor authenticate() should fire."""
        _mock_ytmd_instance.load_token.return_value = "valid-token"
        _mock_ytmd_instance.is_token_valid.return_value = True

        with patch.object(socketio_client.sio, 'connect'), \
             patch.object(socketio_client.sio, 'disconnect'):
            socketio_client._startup_loop()

        _mock_ytmd_instance.clear_token.assert_not_called()
        _mock_ytmd_instance.authenticate.assert_not_called()
        self.assertEqual(state.auth_token, "valid-token")

    def test_stale_loaded_token_triggers_clear(self):
        """Token rejected by YTMD: clear_token() must be called and auth_token cleared."""
        _mock_ytmd_instance.load_token.return_value = "stale-token"
        _mock_ytmd_instance.is_token_valid.return_value = False

        def _do_clear(path):
            state.auth_token = None

        _mock_ytmd_instance.clear_token.side_effect = _do_clear

        with patch.object(socketio_client.sio, 'connect'), \
             patch.object(socketio_client.sio, 'disconnect'):
            socketio_client._startup_loop()

        _mock_ytmd_instance.clear_token.assert_called_once_with(config.TOKEN_FILE)
        self.assertIsNone(state.auth_token)

    def test_no_token_skips_validity_check(self):
        """No token on disk: is_token_valid() must NOT be called (nothing to validate)."""
        _mock_ytmd_instance.load_token.return_value = None

        with patch.object(socketio_client.sio, 'connect'), \
             patch.object(socketio_client.sio, 'disconnect'):
            socketio_client._startup_loop()

        _mock_ytmd_instance.is_token_valid.assert_not_called()

    def test_endpoint_updated_before_auth_check(self):
        """update_endpoint() must be called before any auth or token work."""
        _mock_ytmd_instance.load_token.return_value = None
        state.YTMD_server = "192.168.1.100"

        with patch.object(socketio_client.sio, 'connect'), \
             patch.object(socketio_client.sio, 'disconnect'):
            socketio_client._startup_loop()

        _mock_ytmd_instance.update_endpoint.assert_called_with("192.168.1.100")


# ══════════════════════════════════════════════════════════════════════════════
# TestLogging — config.log() writes to disk
# ══════════════════════════════════════════════════════════════════════════════

class TestLogging(unittest.TestCase):
    """Verify that config.log() creates the log file and appends messages."""

    def test_log_creates_file_on_first_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "test.log")
            original = config.LOG_FILE
            config.LOG_FILE = log_path
            try:
                config.log("hello world")
                self.assertTrue(os.path.exists(log_path))
            finally:
                config.LOG_FILE = original

    def test_log_message_written_to_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "test.log")
            original = config.LOG_FILE
            config.LOG_FILE = log_path
            try:
                config.log("test message")
                with open(log_path, encoding="utf-8") as f:
                    contents = f.read()
                self.assertIn("test message", contents)
            finally:
                config.LOG_FILE = original

    def test_log_appends_multiple_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "test.log")
            original = config.LOG_FILE
            config.LOG_FILE = log_path
            try:
                config.log("line one")
                config.log("line two")
                with open(log_path, encoding="utf-8") as f:
                    contents = f.read()
                self.assertIn("line one", contents)
                self.assertIn("line two", contents)
                # Both messages should be present — no overwrite
                self.assertGreater(contents.count("\n"), 1)
            finally:
                config.LOG_FILE = original

    def test_log_does_not_overwrite_existing_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "test.log")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("pre-existing content\n")
            original = config.LOG_FILE
            config.LOG_FILE = log_path
            try:
                config.log("new entry")
                with open(log_path, encoding="utf-8") as f:
                    contents = f.read()
                self.assertIn("pre-existing content", contents)
                self.assertIn("new entry", contents)
            finally:
                config.LOG_FILE = original


# ══════════════════════════════════════════════════════════════════════════════
# TestEntryTp — entry.tp JSON structure and consistency
# ══════════════════════════════════════════════════════════════════════════════

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

    def test_core_player_states_present(self):
        required = [
            "KillerBOSS.TouchPortal.Plugin.YTMD.States.PlayerTitle",
            "KillerBOSS.TouchPortal.Plugin.YTMD.States.Trackauthor",
            "KillerBOSS.TouchPortal.Plugin.YTMD.States.PlayerisPaused",
            "KillerBOSS.TouchPortal.Plugin.YTMD.States.PlayerhasSong",
            "KillerBOSS.TouchPortal.Plugin.YTMD.States.PlayerVPercent",
            "KillerBOSS.TouchPortal.Plugin.YTMD.States.SeekBarStatus",
            "KillerBOSS.TouchPortal.Plugin.YTMD.States.repeatType",
            "KillerBOSS.TouchPortal.Plugin.YTMD.States.isAdvertisement",
        ]
        for sid in required:
            self.assertIn(sid, self.state_ids, f"Missing state: {sid}")

    def test_debug_states_present(self):
        """TokenPresent, ConnectionDebug, and CoverArtURI must be declared so TP can display them."""
        self.assertIn(
            "KillerBOSS.TouchPortal.Plugin.YTMD.States.TokenPresent",
            self.state_ids,
        )
        self.assertIn(
            "KillerBOSS.TouchPortal.Plugin.YTMD.States.ConnectionDebug",
            self.state_ids,
        )
        self.assertIn(
            "KillerBOSS.TouchPortal.Plugin.YTMD.States.CoverArtURI",
            self.state_ids,
        )

    def test_queue_neighbor_states_present(self):
        for sid in (
            "KillerBOSS.TouchPortal.Plugin.YTMD.States.PreviousSong.title",
            "KillerBOSS.TouchPortal.Plugin.YTMD.States.PreviousSong.author",
            "KillerBOSS.TouchPortal.Plugin.YTMD.States.Next.title",
            "KillerBOSS.TouchPortal.Plugin.YTMD.States.Next.author",
        ):
            self.assertIn(sid, self.state_ids, f"Missing queue-neighbor state: {sid}")

    def test_core_actions_present(self):
        required = [
            "KillerBOSS.TouchPortal.Plugin.YTMD.Action.Play/Pause",
            "KillerBOSS.TouchPortal.Plugin.YTMD.Action.Next/Previous",
            "KillerBOSS.TouchPortal.Plugin.YTMD.Action.Like/Dislike",
            "KillerBOSS.TouchPortal.Plugin.YTMD.Action.SetVolume",
            "KillerBOSS.TouchPortal.Plugin.YTMD.Action.SetSeekBar",
            "KillerBOSS.TouchPortal.Plugin.YTMD.Action.RepeatPic",
            "KillerBOSS.TouchPortal.Plugin.YTMD.Action.AddToPlaylist",
        ]
        for aid in required:
            self.assertIn(aid, self.action_ids, f"Missing action: {aid}")

    def test_at_least_one_category(self):
        self.assertGreater(len(self.entry.get("categories", [])), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
