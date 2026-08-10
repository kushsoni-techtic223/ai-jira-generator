"""Atlassian OAuth 2.0 (3LO) helpers."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import requests

from http_client import external_request

import session_store

AUTH_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"
RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"

# Classic scopes matching Developer Console "Jira platform REST API" + User Identity.
# read:me enables https://api.atlassian.com/me (signed-in profile).
# Console must also enable: Permissions → User identity API → read:me
DEFAULT_SCOPES = "read:jira-work write:jira-work offline_access read:me"

ME_URL = "https://api.atlassian.com/me"

# Console checklist — sharing enables ANY Atlassian user (no collaborators)
DISTRIBUTION_HELP = (
    "In Atlassian Developer Console → your app → Distribution → enable "
    '"Sharing". Do NOT add people as Collaborators. Sharing lets any Jira '
    "user click Connect and authorize their own account."
)

REQUIRED_SCOPES = ("read:jira-work", "write:jira-work", "offline_access", "read:me")


class OAuthError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def oauth_env() -> dict[str, str]:
    return {
        "client_id": os.getenv("JIRA_CLIENT_ID") or "",
        "client_secret": os.getenv("JIRA_CLIENT_SECRET") or "",
        "redirect_uri": os.getenv("JIRA_REDIRECT_URI")
        or "http://localhost:8000/callback",
        "frontend_url": (os.getenv("FRONTEND_URL") or "http://localhost:3000").rstrip(
            "/"
        ),
        "scopes": os.getenv("JIRA_OAUTH_SCOPES") or DEFAULT_SCOPES,
    }


def _state_secret() -> bytes:
    env = oauth_env()
    raw = (env["client_secret"] or env["client_id"] or "dev-state").encode("utf-8")
    return raw


def make_signed_state(session_id: str) -> str:
    """
    Encode session_id in OAuth state with HMAC so callback works even if
    Railway ephemeral disk drops oauth_states.json between login and return.
    """
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
    """What is set in .env vs what the admin must do in Atlassian Console."""
    env = oauth_env()
    missing: list[dict[str, str]] = []
    checks: list[dict[str, Any]] = []

    def add(key: str, ok: bool, label: str, how: str) -> None:
        checks.append({"key": key, "ok": ok, "label": label, "how": how})
        if not ok:
            missing.append({"key": key, "label": label, "how": how})

    add(
        "client_id",
        bool(env["client_id"]),
        "JIRA_CLIENT_ID",
        "Copy Client ID from Developer Console → your app → Settings into backend/.env",
    )
    add(
        "client_secret",
        bool(env["client_secret"]),
        "JIRA_CLIENT_SECRET",
        "Copy Client secret from Developer Console → Settings into backend/.env",
    )
    add(
        "redirect_uri",
        bool(env["redirect_uri"]),
        "Callback URL",
        f"Authorization → OAuth 2.0 (3LO) → Callback URL must be exactly: {env['redirect_uri']}",
    )

    scopes = set((env["scopes"] or "").split())
    for scope in REQUIRED_SCOPES:
        add(
            f"scope_{scope}",
            scope in scopes,
            f"Scope {scope}",
            f"Permissions → add {scope}, and set JIRA_OAUTH_SCOPES in .env to match",
        )

    # Console-only steps we cannot verify from the server
    manual = [
        {
            "key": "permissions",
            "ok": None,
            "label": "Jira platform REST API permissions",
            "how": (
                "Permissions → Jira API → enable read:jira-work + write:jira-work "
                "(+ offline_access). Permissions → User identity API → enable read:me "
                "so the app can call /me. Matching values must be in JIRA_OAUTH_SCOPES."
            ),
        },
        {
            "key": "distribution_sharing",
            "ok": None,
            "label": "Distribution → Sharing ON (required for any user)",
            "how": DISTRIBUTION_HELP,
        },
    ]

    return {
        "oauth_configured": oauth_configured(),
        "redirect_uri": env["redirect_uri"],
        "frontend_url": env["frontend_url"],
        "scopes": env["scopes"],
        "checks": checks,
        "manual_checks": manual,
        "missing": missing,
        "distribution_help": DISTRIBUTION_HELP,
        "console_url": "https://developer.atlassian.com/console/myapps/",
    }


def friendly_oauth_error(error: str | None, description: str | None = None) -> str:
    err = (error or "").lower()
    desc = description or ""
    desc_l = desc.lower()
    if "user identity" in desc_l or "jira & user" in desc_l:
        return (
            "Atlassian blocked login: this account needs Jira Cloud product access "
            "(and User identity comes with read:me). Sign in as the account that opens "
            "your *.atlassian.net site, enable User identity API + read:me in the "
            "Developer Console, or use Connect with API token on the Live board."
        )
    if "jira site" in desc_l and ("don't have" in desc_l or "do not have" in desc_l):
        return (
            "Atlassian says this account has no Jira Cloud site access. "
            "Log in as the account that opens your *.atlassian.net Jira (e.g. work email), "
            "or use Connect with API token on the Live board instead."
        )
    if err in ("access_denied", "unauthorized_client") or "collaborator" in desc_l:
        return (
            "Atlassian blocked this login. The OAuth app is still private. "
            "Open Developer Console → Distribution → enable Sharing "
            "(do not add Collaborators). Then try Connect again. "
            "If you already enabled Sharing, log out of id.atlassian.com, "
            "use the account that has Jira access, and retry in a private window."
        )
    if err == "invalid_scope":
        return (
            "Requested scopes do not match the app. In Developer Console → Permissions, "
            "enable read:jira-work, write:jira-work, offline_access, and read:me "
            "(User identity API), then set the same values in JIRA_OAUTH_SCOPES."
        )
    if description:
        return f"{error}: {description}" if error else description
    return error or "OAuth failed"


def build_authorize_url(session_id: str | None = None) -> tuple[str, str]:
    """Return (authorize_url, session_id)."""
    env = oauth_env()
    if not oauth_configured():
        raise OAuthError(
            "OAuth not configured. Missing in backend/.env: "
            + ", ".join(
                m["label"] for m in setup_checklist()["missing"]
            )
            + ". See /auth/jira/setup for the full checklist."
        )
    sid = session_id or session_store.new_session_id()
    state = make_signed_state(sid)
    # Best-effort file mirror (helps local multi-tab); signed state is source of truth
    try:
        session_store.save_oauth_state(state, sid)
    except OSError:
        pass
    params = {
        "audience": "api.atlassian.com",
        "client_id": env["client_id"],
        "scope": env["scopes"],
        "redirect_uri": env["redirect_uri"],
        "state": state,
        "response_type": "code",
        # Force account picker so the work Atlassian account (with Jira) is used
        "prompt": "login",
    }
    return f"{AUTH_URL}?{urlencode(params)}", sid


def exchange_code(code: str) -> dict[str, Any]:
    env = oauth_env()
    try:
        resp = external_request(
            "POST",
            TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": env["client_id"],
                "client_secret": env["client_secret"],
                "code": code,
                "redirect_uri": env["redirect_uri"],
            },
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise OAuthError(f"Token exchange failed: {exc}", 502) from exc

    if resp.status_code >= 400:
        raise OAuthError(
            f"Token exchange error ({resp.status_code}): {resp.text[:400]}",
            resp.status_code,
        )
    return resp.json()


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    env = oauth_env()
    try:
        resp = external_request(
            "POST",
            TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "client_id": env["client_id"],
                "client_secret": env["client_secret"],
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise OAuthError(f"Token refresh failed: {exc}", 502) from exc

    if resp.status_code >= 400:
        raise OAuthError(
            f"Token refresh error ({resp.status_code}): {resp.text[:400]}",
            resp.status_code,
        )
    return resp.json()


def fetch_accessible_resources(access_token: str) -> list[dict[str, Any]]:
    try:
        resp = external_request(
            "GET",
            RESOURCES_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        raise OAuthError(f"Could not load Jira sites: {exc}", 502) from exc

    if resp.status_code >= 400:
        raise OAuthError(
            f"Accessible resources error ({resp.status_code}): {resp.text[:400]}",
            resp.status_code,
        )
    return resp.json() or []


def fetch_user_profile(access_token: str) -> dict[str, Any]:
    """User Identity API — requires read:me scope."""
    try:
        resp = external_request(
            "GET",
            ME_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        raise OAuthError(f"Could not load user profile: {exc}", 502) from exc

    if resp.status_code >= 400:
        raise OAuthError(
            f"User profile error ({resp.status_code}): {resp.text[:400]}",
            resp.status_code,
        )
    return resp.json() or {}


def complete_login(code: str, state: str | None) -> tuple[dict[str, Any], str]:
    # Prefer HMAC-signed state (works without durable disk on Railway).
    session_id = parse_signed_state(state)
    if not session_id:
        session_id = session_store.pop_oauth_state(state)
    else:
        # Clear file mirror if present
        try:
            session_store.pop_oauth_state(state)
        except OSError:
            pass
    if not session_id:
        raise OAuthError("Invalid OAuth state. Start login again from the app.")

    tokens = exchange_code(code)
    access_token = tokens.get("access_token")
    if not access_token:
        raise OAuthError("No access_token returned from Atlassian.")

    resources = fetch_accessible_resources(access_token)
    if not resources:
        preferred = (os.getenv("JIRA_BASE_URL") or "").rstrip("/")
        hint = (
            f" Your team site is {preferred}."
            if preferred
            else ""
        )
        raise OAuthError(
            "No Jira Cloud sites available for this Atlassian account."
            + hint
            + " Use your @techtic.agency work account (not a personal Gmail Atlassian account), "
            "ask a Jira admin to invite you to the site, then Connect again. "
            "Also ensure the OAuth app has Distribution → Sharing enabled."
        )

    user_email = None
    user_name = None
    account_id = None
    try:
        profile = fetch_user_profile(access_token)
        user_email = profile.get("email")
        user_name = profile.get("name")
        account_id = profile.get("account_id")
    except OAuthError:
        # read:me may not be granted yet — resolve via Jira issues below
        pass

    # Prefer company site (JIRA_BASE_URL) over personal/other Cloud sites
    preferred_url = (os.getenv("JIRA_BASE_URL") or "").rstrip("/").lower()
    site = resources[0]
    if preferred_url:
        match = next(
            (
                r
                for r in resources
                if (r.get("url") or "").rstrip("/").lower() == preferred_url
                or preferred_url in (r.get("url") or "").lower()
            ),
            None,
        )
        if match:
            site = match
        else:
            available = ", ".join(
                (r.get("name") or r.get("url") or "?") for r in resources[:5]
            )
            raise OAuthError(
                f"This Atlassian account is not a member of {preferred_url}. "
                f"Sites on this account: {available or 'none'}. "
                "Log in with the work account that can open that Jira site "
                "(invite required from a site admin)."
            )

    expires_in = int(tokens.get("expires_in") or 3600)
    session = {
        "access_token": access_token,
        "refresh_token": tokens.get("refresh_token"),
        "expires_in": expires_in,
        "expires_at": time.time() + expires_in - 60,
        "scope": tokens.get("scope"),
        "token_type": tokens.get("token_type"),
        "cloud_id": site.get("id"),
        "site_url": (site.get("url") or "").rstrip("/"),
        "site_name": site.get("name"),
        "user_email": user_email,
        "user_name": user_name,
        "account_id": account_id,
        "resources": [
            {
                "id": r.get("id"),
                "url": r.get("url"),
                "name": r.get("name"),
                "scopes": r.get("scopes"),
            }
            for r in resources
        ],
    }

    # Fallback identity from Jira work data (works with read:jira-work only)
    if not user_email and not user_name:
        try:
            import jira_client as jira

            cfg = {
                "auth_type": "oauth",
                "access_token": access_token,
                "cloud_id": session["cloud_id"],
                "base_url": session["site_url"],
                "email": "",
                "api_token": "",
                "project_key": "",
                "board_id": "",
            }
            user = jira.resolve_current_user(cfg)
            session["user_email"] = user.get("email")
            session["user_name"] = user.get("display_name")
            session["account_id"] = user.get("account_id") or account_id
        except Exception:
            pass

    session_store.save_session(session_id, session)
    return session, session_id


def ensure_fresh_session(session_id: str | None) -> dict[str, Any]:
    """Return session, refreshing only when near expiry."""
    session = session_store.get_session(session_id)
    if not session:
        raise OAuthError("Not connected to Jira. Click Connect with Jira first.", 401)

    # API-token sessions do not expire via OAuth refresh
    if (session.get("auth_type") or "oauth") == "basic":
        if not (session.get("site_url") and session.get("email") and session.get("api_token")):
            raise OAuthError("API token session is incomplete. Reconnect.", 401)
        return session

    expires_at = float(session.get("expires_at") or 0)
    if expires_at > 0 and time.time() >= expires_at and session.get("refresh_token"):
        try:
            tokens = refresh_access_token(session["refresh_token"])
            session["access_token"] = tokens.get("access_token") or session["access_token"]
            if tokens.get("refresh_token"):
                session["refresh_token"] = tokens["refresh_token"]
            expires_in = int(tokens.get("expires_in") or 3600)
            session["expires_in"] = expires_in
            session["expires_at"] = time.time() + expires_in - 60
            session_store.save_session(session_id, session)  # type: ignore[arg-type]
        except OAuthError:
            pass

    return session


def complete_api_token_login(
    base_url: str,
    email: str,
    api_token: str,
    session_id: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Connect via Jira email + API token (bypasses OAuth consent)."""
    import jira_client as jira

    url = (base_url or "").strip().rstrip("/")
    mail = (email or "").strip()
    token = (api_token or "").strip()
    if not url or not mail or not token:
        raise OAuthError("Site URL, email, and API token are all required.", 400)
    if not url.startswith("http"):
        url = f"https://{url}"

    cfg = {
        "auth_type": "basic",
        "base_url": url,
        "email": mail,
        "api_token": token,
        "access_token": "",
        "cloud_id": "",
        "project_key": "",
        "board_id": "",
    }
    try:
        # Must succeed against /myself — do not use soft resolve_current_user fallback
        health = jira.health_check(cfg)
    except jira.JiraApiError as exc:
        raise OAuthError(
            f"API token login failed ({exc.status_code}): {exc}. "
            "Use the Atlassian account email that can open your Jira site, "
            "and a token from https://id.atlassian.com/manage-profile/security/api-tokens",
            exc.status_code if 400 <= exc.status_code < 600 else 401,
        ) from exc
    except jira.JiraConfigError as exc:
        raise OAuthError(str(exc), 400) from exc

    sid = session_id or session_store.new_session_id()
    site_name = url.replace("https://", "").replace("http://", "").split(".")[0]
    session = {
        "auth_type": "basic",
        "base_url": url,
        "site_url": url,
        "site_name": site_name,
        "email": mail,
        "api_token": token,
        "user_email": health.get("email") or mail,
        "user_name": health.get("display_name"),
        "account_id": health.get("account_id"),
        "cloud_id": "",
        "access_token": "",
        "resources": [{"id": "", "url": url, "name": site_name, "scopes": []}],
    }
    session_store.save_session(sid, session)
    return session, sid


def session_to_cfg(session: dict[str, Any] | None = None, session_id: str | None = None) -> dict[str, str]:
    if session is None:
        session = ensure_fresh_session(session_id)

    if (session.get("auth_type") or "oauth") == "basic":
        base = (session.get("site_url") or session.get("base_url") or "").rstrip("/")
        email = session.get("email") or ""
        api_token = session.get("api_token") or ""
        if not (base and email and api_token):
            raise OAuthError("API token session is incomplete. Reconnect.", 401)
        return {
            "auth_type": "basic",
            "access_token": "",
            "cloud_id": "",
            "base_url": base,
            "email": email,
            "api_token": api_token,
            "project_key": "",
            "board_id": "",
        }

    cloud_id = session.get("cloud_id") or ""
    if not cloud_id:
        raise OAuthError("OAuth session is missing cloud_id. Reconnect with Jira.", 401)
    access_token = session.get("access_token") or ""
    if not access_token:
        raise OAuthError("OAuth session is missing access_token. Reconnect with Jira.", 401)
    return {
        "auth_type": "oauth",
        "access_token": access_token,
        "cloud_id": cloud_id,
        "base_url": session.get("site_url") or "",
        "email": "",
        "api_token": "",
        "project_key": "",
        "board_id": "",
    }
