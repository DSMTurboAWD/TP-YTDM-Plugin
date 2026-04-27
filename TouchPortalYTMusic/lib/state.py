"""
    Mutable shared plugin state. Import this module and mutate attributes directly:
    import state
    state.auth_token = token
"""

auth_token:             str   = None
YTMD_server:            str   = "localhost"
isYTMDRunning:          bool  = False
running:                bool  = False
playlist_id_map:        dict  = {}
current_video_progress: float = 0.0
