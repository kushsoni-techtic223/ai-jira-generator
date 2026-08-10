"""File-backed store for GitHub OAuth sessions (separate from Jira)."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SESSIONS_PATH = os.path.join(DATA_DIR, "github_sessions.json")
STATE_PATH = os.path.join(DATA_DIR, "github_oauth_states.json")

_lock = threading.Lock()


def _ensure_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: str, default: Any) -> Any:
    _ensure_dir()
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _write(path: str, data: Any) -> None:
    _ensure_dir()
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def new_session_id() -> str:
    return str(uuid.uuid4())


def save_oauth_state(state: str, session_id: str) -> None:
    with _lock:
        states = _read(STATE_PATH, {})
        if not isinstance(states, dict):
            states = {}
        states[state] = {"session_id": session_id, "created_at": _now()}
        _write(STATE_PATH, states)


def pop_oauth_state(state: str | None) -> str | None:
    if not state:
        return None
    with _lock:
        states = _read(STATE_PATH, {})
        if not isinstance(states, dict):
            return None
        entry = states.pop(state, None)
        _write(STATE_PATH, states)
        if isinstance(entry, dict):
            return entry.get("session_id")
        return None


def save_session(session_id: str, session: dict[str, Any]) -> None:
    with _lock:
        sessions = _read(SESSIONS_PATH, {})
        if not isinstance(sessions, dict):
            sessions = {}
        sessions[session_id] = {
            **session,
            "session_id": session_id,
            "updated_at": _now(),
        }
        _write(SESSIONS_PATH, sessions)


def get_session(session_id: str | None) -> dict[str, Any] | None:
    if not session_id:
        return None
    with _lock:
        sessions = _read(SESSIONS_PATH, {})
        if not isinstance(sessions, dict):
            return None
        data = sessions.get(session_id)
        if not data or not data.get("access_token"):
            return None
        return data


def clear_session(session_id: str | None) -> None:
    if not session_id:
        return
    with _lock:
        sessions = _read(SESSIONS_PATH, {})
        if isinstance(sessions, dict) and session_id in sessions:
            del sessions[session_id]
            _write(SESSIONS_PATH, sessions)
