"""Local GitHub-daily timer sessions (separate from Jira worklogs)."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
PATH = os.path.join(DATA_DIR, "github_local_sessions.json")
ACTIVE_PATH = os.path.join(DATA_DIR, "github_local_active_timer.json")

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


def get_active(owner_key: str) -> dict[str, Any] | None:
    with _lock:
        data = _read(ACTIVE_PATH, {})
        if not isinstance(data, dict):
            return None
        entry = data.get(owner_key)
        return entry if isinstance(entry, dict) else None


def set_active(owner_key: str, timer: dict[str, Any] | None) -> None:
    with _lock:
        data = _read(ACTIVE_PATH, {})
        if not isinstance(data, dict):
            data = {}
        if timer is None:
            data.pop(owner_key, None)
        else:
            data[owner_key] = timer
        _write(ACTIVE_PATH, data)


def append_session(entry: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        rows = _read(PATH, [])
        if not isinstance(rows, list):
            rows = []
        saved = {
            **entry,
            "id": entry.get("id") or str(uuid.uuid4()),
            "saved_at": entry.get("saved_at") or _now(),
        }
        rows.append(saved)
        _write(PATH, rows)
        return saved


def list_sessions(
    *,
    owner_key: str | None = None,
    day: str | None = None,
    tz_name: str = "Asia/Kolkata",
    limit: int = 200,
) -> list[dict[str, Any]]:
    with _lock:
        rows = _read(PATH, [])
    if not isinstance(rows, list):
        return []
    tz = ZoneInfo(tz_name)
    out: list[dict[str, Any]] = []
    for row in rows:
        if owner_key and row.get("owner_key") != owner_key:
            continue
        if day:
            started = row.get("started_at") or ""
            try:
                raw = started.replace("Z", "+00:00")
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                local_day = dt.astimezone(tz).date().isoformat()
                if local_day != day:
                    continue
            except ValueError:
                continue
        out.append(row)
    out.sort(key=lambda r: r.get("started_at") or "")
    return out[-limit:]
