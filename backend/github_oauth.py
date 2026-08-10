"""GitHub OAuth App helpers (Connect without pasting a PAT)."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode

from http_client import external_request
import github_session_store as gh_sessions

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"

# repo = private+public repos; read:user = profile; read:org = org membership/repos
DEFAULT_SCOPES = "read:user read:org repo"


class GitHubOAuthError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def oauth_env() -> dict[str, str]:
    return {
        "client_id": (os.getenv("GITHUB_CLIENT_ID") or "").strip(),
        "client_secret": (os.getenv("GITHUB_CLIENT_SECRET") or "").strip(),
        "redirect_uri": (
            os.getenv("GITHUB_REDIRECT_URI")
            or "http://localhost:8000/auth/github/callback"
        ).strip(),
        "frontend_url": (os.getenv("FRONTEND_URL") or "http://localhost:3000").rstrip(
            "/"
        ),
        "scopes": (os.getenv("GITHUB_OAUTH_SCOPES") or DEFAULT_SCOPES).strip(),
    }


def _state_secret() -> bytes:
    env = oauth_env()
    return (env["client_secret"] or env["client_id"] or "gh-dev-state").encode("utf-8")


def make_signed_state(session_id: str) -> str:
    nonce = secrets.token_urlsafe(12)
    payload = f"{session_id}.{nonce}"
    sig = hmac.new(_state_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()[
        :32
    ]
    return f"{payload}.{sig}"


def parse_signed_state(state: str | None) -> str | None:
    if not state or state.count(".") < 2:
        return None
    session_id, nonce, sig = state.rsplit(".", 2)
    if not session_id or not nonce or not sig:
        return None
    payload = f"{session_id}.{nonce}"
    expected = hmac.new(
        _state_secret(), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected):
        return None
    return session_id


def oauth_configured() -> bool:
    env = oauth_env()
    return bool(env["client_id"] and env["client_secret"])


def setup_checklist() -> dict[str, Any]:
    env = oauth_env()
    checks = [
        {
            "key": "client_id",
            "ok": bool(env["client_id"]),
            "label": "GITHUB_CLIENT_ID",
            "how": "GitHub → Settings → Developer settings → OAuth Apps → create app → Client ID",
        },
        {
            "key": "client_secret",
            "ok": bool(env["client_secret"]),
            "label": "GITHUB_CLIENT_SECRET",
            "how": "Same OAuth App → generate a new client secret",
        },
        {
            "key": "redirect_uri",
            "ok": bool(env["redirect_uri"]),
            "label": "Authorization callback URL",
            "how": f"Must match exactly: {env['redirect_uri']}",
        },
    ]
    missing = [c for c in checks if not c["ok"]]
    return {
        "oauth_configured": oauth_configured(),
        "redirect_uri": env["redirect_uri"],
        "scopes": env["scopes"],
        "checks": checks,
        "missing": missing,
        "create_app_url": "https://github.com/settings/developers",
    }


def build_authorize_url(session_id: str | None = None) -> tuple[str, str]:
    if not oauth_configured():
        raise GitHubOAuthError(
            "GitHub OAuth is not configured. Set GITHUB_CLIENT_ID and "
            "GITHUB_CLIENT_SECRET in backend/.env (create an OAuth App at "
            "https://github.com/settings/developers)."
        )
    env = oauth_env()
    sid = session_id or gh_sessions.new_session_id()
    state = make_signed_state(sid)
    try:
        gh_sessions.save_oauth_state(state, sid)
    except OSError:
        pass
    params = {
        "client_id": env["client_id"],
        "redirect_uri": env["redirect_uri"],
        "scope": env["scopes"],
        "state": state,
        "allow_signup": "true",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}", sid


def exchange_code(code: str) -> dict[str, Any]:
    env = oauth_env()
    resp = external_request(
        "POST",
        TOKEN_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "ai-jira-generator-github-oauth",
        },
        json={
            "client_id": env["client_id"],
            "client_secret": env["client_secret"],
            "code": code,
            "redirect_uri": env["redirect_uri"],
        },
        timeout=30,
    )
    if resp.status_code >= 400:
        raise GitHubOAuthError(
            f"GitHub token exchange failed: {(resp.text or '')[:240]}",
            status_code=resp.status_code,
        )
    data = resp.json() if resp.content else {}
    if data.get("error"):
        raise GitHubOAuthError(
            data.get("error_description") or data.get("error") or "OAuth error",
            status_code=400,
        )
    token = (data.get("access_token") or "").strip()
    if not token:
        raise GitHubOAuthError("GitHub did not return an access token.")
    return data


def fetch_user(access_token: str) -> dict[str, Any]:
    resp = external_request(
        "GET",
        USER_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-jira-generator-github-oauth",
        },
        timeout=30,
    )
    if resp.status_code >= 400:
        raise GitHubOAuthError(
            f"Could not load GitHub user: {(resp.text or '')[:240]}",
            status_code=resp.status_code,
        )
    data = resp.json()
    return {
        "login": (data.get("login") or "").strip(),
        "name": (data.get("name") or "").strip(),
        "email": (data.get("email") or "").strip(),
        "avatar_url": data.get("avatar_url"),
        "html_url": data.get("html_url"),
        "id": data.get("id"),
    }


def complete_login(code: str, state: str | None) -> tuple[dict[str, Any], str]:
    sid = parse_signed_state(state)
    if not sid:
        sid = gh_sessions.pop_oauth_state(state)
    else:
        try:
            gh_sessions.pop_oauth_state(state)
        except OSError:
            pass
    if not sid:
        raise GitHubOAuthError("Invalid or expired OAuth state. Try Connect again.")
    token_payload = exchange_code(code)
    access_token = token_payload["access_token"]
    profile = fetch_user(access_token)
    if not profile.get("login"):
        raise GitHubOAuthError("GitHub user profile missing login.")
    session = {
        "auth_type": "oauth",
        "access_token": access_token,
        "token_type": token_payload.get("token_type") or "bearer",
        "scope": token_payload.get("scope") or "",
        "username": profile["login"],
        "user_name": profile.get("name") or profile["login"],
        "user_email": profile.get("email") or "",
        "avatar_url": profile.get("avatar_url"),
        "html_url": profile.get("html_url"),
        "github_user_id": profile.get("id"),
        "created_at": time.time(),
    }
    gh_sessions.save_session(sid, session)
    return session, sid
