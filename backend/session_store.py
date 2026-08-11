"""File-backed store for per-user OAuth sessions and local work time logs."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SESSIONS_PATH = os.path.join(DATA_DIR, "oauth_sessions.json")
# Legacy single-session file (migrated on first read)
LEGACY_TOKEN_PATH = os.path.join(DATA_DIR, "oauth_session.json")
WORKLOG_PATH = os.path.join(DATA_DIR, "worklogs.json")
STATE_PATH = os.path.join(DATA_DIR, "oauth_states.json")

_lock = threading.Lock()


def _ensure_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_sessions() -> dict[str, Any]:
    data = _read(SESSIONS_PATH, None)
    if isinstance(data, dict) and data:
        return data

    # Migrate legacy single-session file once
    legacy = _read(LEGACY_TOKEN_PATH, None)
    if isinstance(legacy, dict) and legacy.get("access_token"):
        sid = legacy.get("session_id") or str(uuid.uuid4())
        migrated = {sid: {**legacy, "session_id": sid, "updated_at": _now()}}
        _write(SESSIONS_PATH, migrated)
        try:
            os.remove(LEGACY_TOKEN_PATH)
        except OSError:
            pass
        return migrated
    return {}


def new_session_id() -> str:
    return str(uuid.uuid4())


def save_oauth_state(state: str, session_id: str) -> None:
    with _lock:
        states = _read(STATE_PATH, {})
        if not isinstance(states, dict):
            states = {}
        # Drop single-key legacy shape
        if "state" in states and "session_id" in states and len(states) <= 3:
            states = {}
        states[state] = {"session_id": session_id, "created_at": _now()}
        _write(STATE_PATH, states)


def pop_oauth_state(state: str | None) -> str | None:
    """Return session_id for this OAuth state, or None if invalid."""
    if not state:
        return None
    with _lock:
        states = _read(STATE_PATH, {})
        # Legacy single state file
        if isinstance(states, dict) and states.get("state") == state:
            sid = states.get("session_id")
            try:
                os.remove(STATE_PATH)
            except OSError:
                pass
            return sid
        if not isinstance(states, dict):
            return None
        entry = states.pop(state, None)
        _write(STATE_PATH, states)
        if isinstance(entry, dict):
            return entry.get("session_id")
        return None


def save_session(session_id: str, session: dict[str, Any]) -> None:
    with _lock:
        sessions = _load_sessions()
        sessions[session_id] = {
            **session,
            "session_id": session_id,
            "updated_at": _now(),
        }
        _write(SESSIONS_PATH, sessions)


def _is_valid_session(data: dict[str, Any]) -> bool:
    auth_type = (data.get("auth_type") or "oauth").lower()
    if auth_type == "basic":
        has_site = bool((data.get("site_url") or data.get("base_url") or "").strip())
        has_email = bool((data.get("email") or "").strip())
        has_token = bool((data.get("api_token") or "").strip())
        return has_site and has_email and has_token
    return bool(data.get("access_token"))


def get_session(session_id: str | None) -> dict[str, Any] | None:
    if not session_id:
        return None
    with _lock:
        sessions = _load_sessions()
        data = sessions.get(session_id)
        if not data or not _is_valid_session(data):
            return None
        return data


def clear_session(session_id: str | None) -> None:
    if not session_id:
        return
    with _lock:
        sessions = _load_sessions()
        if session_id in sessions:
            del sessions[session_id]
            _write(SESSIONS_PATH, sessions)


def append_worklog(entry: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        logs = _read(WORKLOG_PATH, [])
        entry = {
            **entry,
            "id": entry.get("id") or str(uuid.uuid4()),
            "logged_at": entry.get("logged_at") or _now(),
        }
        logs.append(entry)
        _write(WORKLOG_PATH, logs)
        return entry


def update_worklog(worklog_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    with _lock:
        logs = _read(WORKLOG_PATH, [])
        updated = None
        for i, entry in enumerate(logs):
            if entry.get("id") == worklog_id:
                logs[i] = {**entry, **patch}
                updated = logs[i]
                break
        if updated is not None:
            _write(WORKLOG_PATH, logs)
        return updated


def list_worklogs(
    *,
    issue_key: str | None = None,
    account_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    with _lock:
        logs = _read(WORKLOG_PATH, [])
    if issue_key:
        logs = [x for x in logs if x.get("issue_key") == issue_key]
    if account_id:
        logs = [x for x in logs if x.get("account_id") == account_id]
    logs = sorted(logs, key=lambda x: x.get("logged_at") or "", reverse=True)
    return logs[:limit]


def worklog_totals_by_issue(account_id: str | None = None) -> dict[str, int]:
    """Seconds logged per issue key (local store)."""
    with _lock:
        logs = _read(WORKLOG_PATH, [])
    totals: dict[str, int] = {}
    for entry in logs:
        if account_id and entry.get("account_id") != account_id:
            continue
        key = entry.get("issue_key")
        if not key:
            continue
        totals[key] = totals.get(key, 0) + int(entry.get("seconds") or 0)
    return totals
