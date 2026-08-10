import os
import shutil
import smtplib
import subprocess
import tempfile
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.policy import SMTP
from typing import Any, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

import http_client  # noqa: F401 — disable Cursor proxy before outbound HTTP

from fastapi import FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

try:
    from dotenv import load_dotenv

    # override=True so .env edits apply on uvicorn --reload (parent env otherwise sticks)
    load_dotenv(override=True)
except ImportError:
    pass

from ai import generate_jira_data
from parser import parse_docx, parse_pdf
import jira_client as jira
import jira_oauth
import session_store
import github_client as gh
import github_daily
import github_oauth
import github_session_store as gh_sessions
import github_local_store as gh_local
import local_git

app = FastAPI(title="Daily Time Logger")

_FRONTEND = (os.getenv("FRONTEND_URL") or "http://localhost:3000").rstrip("/")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_FRONTEND, "http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSION_HEADER = "X-Jira-Session"
GITHUB_SESSION_HEADER = "X-Github-Session"

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


class JiraCredentials(BaseModel):
    base_url: Optional[str] = Field(None, description="https://your-domain.atlassian.net")
    email: Optional[str] = None
    api_token: Optional[str] = None
    project_key: Optional[str] = None
    board_id: Optional[str] = None


class JiraFetchRequest(JiraCredentials):
    jql: Optional[str] = None
    max_results: int = Field(1000, ge=1, le=2000)
    mode: Optional[str] = Field(
        None,
        description="board | today | my_open | project — used when jql is omitted",
    )
    use_oauth: bool = True


class WorklogRequest(BaseModel):
    issue_key: str
    seconds: int = Field(..., ge=1)
    started_at: str
    ended_at: Optional[str] = None
    comment: Optional[str] = "Worked via AI Jira Generator timer"
    push_to_jira: bool = True


class DailyEmailRequest(BaseModel):
    date: Optional[str] = Field(
        None, description="YYYY-MM-DD in report timezone (default: today)"
    )
    to: Optional[list[str]] = None
    cc: Optional[list[str]] = None


class GitHubDailyRequest(BaseModel):
    """Per-user GitHub credentials + optional daily email fields."""

    date: Optional[str] = Field(
        None, description="YYYY-MM-DD in report timezone (default: today)"
    )
    to: Optional[list[str]] = None
    cc: Optional[list[str]] = None
    github_token: Optional[str] = Field(
        None, description="User PAT — preferred over GITHUB_TOKEN env"
    )
    github_username: Optional[str] = Field(
        None, description="GitHub login — preferred over GITHUB_USERNAME env"
    )
    github_repos: Optional[str] = Field(
        None,
        description="Comma-separated owner/repo list (optional; empty = search API)",
    )
    author_email: Optional[str] = None
    display_name: Optional[str] = Field(
        None, description="Name shown in Regards / subject"
    )


class GitHubConnectRequest(BaseModel):
    github_token: Optional[str] = None
    github_username: Optional[str] = None
    github_repos: Optional[str] = None


class GitHubLocalRepoRequest(BaseModel):
    repo_path: str
    branch: Optional[str] = None
    owner_key: Optional[str] = Field(
        None, description="Browser id / github username to scope timers"
    )


class GitHubLocalTimerStartRequest(BaseModel):
    repo_path: str
    branch: Optional[str] = None
    owner_key: str = Field(..., min_length=1)
    display_name: Optional[str] = None


class GitHubLocalTimerStopRequest(BaseModel):
    owner_key: str = Field(..., min_length=1)
    author: Optional[str] = Field(
        None, description="Optional git --author filter (name or email)"
    )
    commit_message: Optional[str] = Field(
        None,
        description="If set, create a local commit before stopping the timer",
    )
    add_all: bool = True
    push: bool = True
    github_token: Optional[str] = None


class GitHubLocalCommitRequest(BaseModel):
    owner_key: Optional[str] = None
    repo_path: Optional[str] = None
    branch: Optional[str] = None
    message: str = Field(..., min_length=1)
    add_all: bool = True
    push: bool = True
    github_token: Optional[str] = None


class GitHubLocalPushRequest(BaseModel):
    owner_key: Optional[str] = None
    repo_path: Optional[str] = None
    branch: Optional[str] = None
    github_token: Optional[str] = None


class GitHubLocalTimerPreviewRequest(BaseModel):
    date: Optional[str] = None
    owner_key: str = Field(..., min_length=1)
    to: Optional[list[str]] = None
    cc: Optional[list[str]] = None
    display_name: Optional[str] = None


class SelectSiteRequest(BaseModel):
    cloud_id: str


class ApiTokenLoginRequest(BaseModel):
    base_url: str = Field(..., description="https://your-domain.atlassian.net")
    email: str
    api_token: str


class TransitionRequest(BaseModel):
    target_status: Optional[str] = Field(
        None,
        description="Jira status name or board column: To Do | Working now | Dev done | Done after QA verified",
    )
    transition_id: Optional[str] = None


def _cfg(
    body: JiraCredentials | None,
    *,
    prefer_oauth: bool = True,
    session_id: str | None = None,
) -> dict[str, str]:
    overrides = body.model_dump(exclude_none=True) if body else {}
    use_oauth = prefer_oauth and overrides.pop("use_oauth", True) is not False

    if use_oauth and session_id and session_store.get_session(session_id):
        try:
            oauth_cfg = jira_oauth.session_to_cfg(session_id=session_id)
            # Allow project_key / board_id overrides from request
            for key in ("project_key", "board_id"):
                if overrides.get(key):
                    oauth_cfg[key] = overrides[key]
            return oauth_cfg
        except jira_oauth.OAuthError:
            pass

    return jira.resolve_config(overrides or None)


def _handle_jira_errors(exc: Exception) -> None:
    if isinstance(exc, jira.JiraConfigError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, jira.JiraApiError):
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if isinstance(exc, jira_oauth.OAuthError):
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    raise exc


@app.post("/generate-jira")
async def generate_jira(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    file_path = f"{UPLOAD_DIR}/{file.filename}"
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    text = ""
    if file.filename.endswith(".docx"):
        text = parse_docx(file_path)
    elif file.filename.endswith(".pdf"):
        text = parse_pdf(file_path)
    else:
        raise HTTPException(status_code=400, detail="Only .pdf or .docx supported")

    data = generate_jira_data(text)
    return data


# ── OAuth 3LO ───────────────────────────────────────────────────────────────


@app.get("/auth/jira/setup")
def jira_oauth_setup():
    """Show which .env / console settings are missing so any admin can fill them in."""
    return jira_oauth.setup_checklist()


@app.get("/auth/jira/login")
def jira_oauth_login(request: Request):
    """Redirect browser to Atlassian consent screen."""
    try:
        # Optional: resume same browser session id from query
        sid = request.query_params.get("sid") or None
        url, sid = jira_oauth.build_authorize_url(sid)
        return RedirectResponse(url)
    except Exception as exc:
        _handle_jira_errors(exc)


@app.get("/callback")
def jira_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    """OAuth redirect URI registered in Atlassian Developer Console."""
    frontend = jira_oauth.oauth_env()["frontend_url"]
    if error:
        msg = jira_oauth.friendly_oauth_error(error, error_description)
        return RedirectResponse(f"{frontend}?tab=live&jira_error={quote(msg)}")
    if not code:
        return RedirectResponse(f"{frontend}?tab=live&jira_error=missing_code")
    try:
        _session, sid = jira_oauth.complete_login(code, state)
        return RedirectResponse(
            f"{frontend}?tab=live&jira=connected&sid={quote(sid)}"
        )
    except Exception as exc:
        msg = str(exc)
        return RedirectResponse(f"{frontend}?tab=live&jira_error={quote(msg)}")


@app.get("/auth/jira/status")
def jira_oauth_status(session_id: Optional[str] = Header(None, alias=SESSION_HEADER)):
    setup = jira_oauth.setup_checklist()
    session = session_store.get_session(session_id)
    if not session:
        return {
            "connected": False,
            "oauth_configured": jira_oauth.oauth_configured(),
            "redirect_uri": setup["redirect_uri"],
            "setup": setup,
        }

    user_email = session.get("user_email")
    user_name = session.get("user_name")

    # Backfill profile if missing
    if (not user_email and not user_name) and session.get("access_token"):
        try:
            profile = jira_oauth.fetch_user_profile(session["access_token"])
            user_email = profile.get("email") or user_email
            user_name = profile.get("name") or user_name
            session["user_email"] = user_email
            session["user_name"] = user_name
            session["account_id"] = profile.get("account_id") or session.get("account_id")
            if session_id:
                session_store.save_session(session_id, session)
        except Exception:
            try:
                cfg = jira_oauth.session_to_cfg(session)
                user = jira.resolve_current_user(cfg)
                user_email = user.get("email") or user_email
                user_name = user.get("display_name") or user_name
                session["user_email"] = user_email
                session["user_name"] = user_name
                session["account_id"] = user.get("account_id") or session.get("account_id")
                if session_id:
                    session_store.save_session(session_id, session)
            except Exception:
                pass

    return {
        "connected": True,
        "oauth_configured": True,
        "session_id": session_id,
        "auth_type": session.get("auth_type") or "oauth",
        "user_email": user_email,
        "user_name": user_name,
        "account_id": session.get("account_id"),
        "site_name": session.get("site_name"),
        "site_url": session.get("site_url"),
        "cloud_id": session.get("cloud_id"),
        "resources": session.get("resources") or [],
        "redirect_uri": setup["redirect_uri"],
        "setup": setup,
    }


@app.post("/auth/jira/logout")
def jira_oauth_logout(session_id: Optional[str] = Header(None, alias=SESSION_HEADER)):
    session_store.clear_session(session_id)
    return {"ok": True}


@app.post("/auth/jira/api-token")
def jira_api_token_login(
    body: ApiTokenLoginRequest,
    session_id: Optional[str] = Header(None, alias=SESSION_HEADER),
):
    """Bypass OAuth — connect with site URL + email + Atlassian API token."""
    try:
        session, sid = jira_oauth.complete_api_token_login(
            body.base_url,
            body.email,
            body.api_token,
            session_id=session_id,
        )
        return {
            "ok": True,
            "connected": True,
            "session_id": sid,
            "auth_type": "basic",
            "user_email": session.get("user_email"),
            "user_name": session.get("user_name"),
            "site_url": session.get("site_url"),
            "site_name": session.get("site_name"),
            "account_id": session.get("account_id"),
        }
    except Exception as exc:
        _handle_jira_errors(exc)


@app.post("/auth/jira/site")
def jira_select_site(
    body: SelectSiteRequest,
    session_id: Optional[str] = Header(None, alias=SESSION_HEADER),
):
    session = session_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Not connected")
    match = next(
        (r for r in (session.get("resources") or []) if r.get("id") == body.cloud_id),
        None,
    )
    if not match:
        raise HTTPException(status_code=404, detail="Site not found in authorized resources")
    session["cloud_id"] = match["id"]
    session["site_url"] = (match.get("url") or "").rstrip("/")
    session["site_name"] = match.get("name")
    if session_id:
        session_store.save_session(session_id, session)
    return {"ok": True, "cloud_id": session["cloud_id"], "site_url": session["site_url"]}


# ── Connected account APIs ──────────────────────────────────────────────────


@app.get("/jira/config")
def jira_config_status(session_id: Optional[str] = Header(None, alias=SESSION_HEADER)):
    env = jira.resolve_config(None)
    session = session_store.get_session(session_id)
    setup = jira_oauth.setup_checklist()
    return {
        "configured": jira.has_credentials(env) or bool(session),
        "oauth_connected": bool(session),
        "oauth_configured": jira_oauth.oauth_configured(),
        "base_url": (session or {}).get("site_url") or env.get("base_url") or None,
        "email": env.get("email") or None,
        "project_key": env.get("project_key") or None,
        "board_id": env.get("board_id") or None,
        "has_api_token": bool(env.get("api_token")),
        "setup": setup,
    }


@app.post("/jira/health")
def jira_health(
    body: JiraCredentials = JiraCredentials(),
    session_id: Optional[str] = Header(None, alias=SESSION_HEADER),
):
    try:
        cfg = _cfg(body, session_id=session_id)
        return jira.health_check(cfg)
    except Exception as exc:
        _handle_jira_errors(exc)


@app.get("/jira/projects")
def jira_projects(session_id: Optional[str] = Header(None, alias=SESSION_HEADER)):
    """List projects the logged-in user is connected to (not every site project)."""
    try:
        cfg = _cfg(None, session_id=session_id)
        projects = jira.list_my_projects(cfg)
        session = session_store.get_session(session_id) or {}
        user_email = session.get("user_email")
        user_name = session.get("user_name")

        if not user_email and not user_name:
            user = jira.resolve_current_user(cfg)
            user_email = user.get("email")
            user_name = user.get("display_name")
            if (user_email or user_name) and session_id:
                session["user_email"] = user_email
                session["user_name"] = user_name
                session["account_id"] = user.get("account_id") or session.get("account_id")
                session_store.save_session(session_id, session)

        return {
            "projects": projects,
            "count": len(projects),
            "user": user_email or user_name,
            "user_email": user_email,
            "user_name": user_name,
            "site_url": cfg.get("base_url"),
            "site_name": session.get("site_name"),
            "filter": "currentUser",
        }
    except Exception as exc:
        _handle_jira_errors(exc)


@app.get("/jira/project/{project_key}/statuses")
def jira_project_statuses(
    project_key: str,
    session_id: Optional[str] = Header(None, alias=SESSION_HEADER),
):
    """Workflow statuses for a project's board (column order)."""
    try:
        cfg = _cfg(None, session_id=session_id)
        statuses = jira.get_project_workflow_statuses(cfg, project_key)
        return {"project_key": project_key, "statuses": statuses, "count": len(statuses)}
    except Exception as exc:
        _handle_jira_errors(exc)


@app.get("/jira/project/{project_key}/issues")
def jira_project_issues(
    project_key: str,
    max_results: int = Query(1000, ge=1, le=2000),
    current_sprint: bool = Query(
        True,
        description="If true, only issues in open/current sprint(s)",
    ),
    session_id: Optional[str] = Header(None, alias=SESSION_HEADER),
):
    """Fetch issues for one project into our board shape (paginated from Jira)."""
    try:
        cfg = _cfg(None, session_id=session_id)
        cfg["project_key"] = project_key
        jql = jira.build_project_jql(project_key, current_sprint=current_sprint)
        issues = jira.search_issues(cfg, jql, max_results=max_results)
        data = jira.issues_to_jira_data(
            issues,
            project_name=project_key,
            base_url=cfg.get("base_url") or "",
        )
        session = session_store.get_session(session_id) or {}
        totals = session_store.worklog_totals_by_issue(session.get("account_id"))
        data["logged_seconds"] = totals
        data["mode"] = "current_sprint" if current_sprint else "project"
        data["jql"] = jql
        data["current_sprint"] = current_sprint
        data["fetched"] = len(issues)
        data["max_results"] = max_results
        data["may_have_more"] = len(issues) >= max_results
        data["jira_statuses"] = jira.get_project_workflow_statuses(cfg, project_key)
        data["board_id"] = cfg.get("board_id") or None
        return data
    except Exception as exc:
        _handle_jira_errors(exc)


@app.post("/jira/boards")
def jira_boards(
    body: JiraCredentials = JiraCredentials(),
    session_id: Optional[str] = Header(None, alias=SESSION_HEADER),
):
    try:
        cfg = _cfg(body, session_id=session_id)
        boards = jira.list_boards(cfg, cfg.get("project_key") or None)
        return {"boards": boards, "count": len(boards)}
    except Exception as exc:
        _handle_jira_errors(exc)


@app.post("/jira/issues")
def jira_issues(
    body: JiraFetchRequest,
    session_id: Optional[str] = Header(None, alias=SESSION_HEADER),
):
    try:
        cfg = _cfg(body, prefer_oauth=body.use_oauth, session_id=session_id)
        mode = (body.mode or "").lower().strip()
        issues: list[dict[str, Any]] = []
        project_name = cfg.get("project_key") or "Jira Board"

        if body.jql:
            issues = jira.search_issues(cfg, body.jql, max_results=body.max_results)
            project_name = cfg.get("project_key") or "Custom JQL"
        elif mode == "board" or (not mode and cfg.get("board_id")):
            board_id = cfg.get("board_id")
            if not board_id:
                raise jira.JiraConfigError("board_id is required for board mode")
            issues = jira.fetch_board_issues(cfg, board_id, max_results=body.max_results)
            try:
                boards = jira.list_boards(cfg, cfg.get("project_key") or None)
                match = next((b for b in boards if str(b.get("id")) == str(board_id)), None)
                if match:
                    project_name = match.get("name") or project_name
            except Exception:
                project_name = f"Board {board_id}"
        elif mode == "today":
            jql = jira.build_today_jql(cfg.get("project_key") or None)
            issues = jira.search_issues(cfg, jql, max_results=body.max_results)
            project_name = "Today's work"
        elif mode == "my_open":
            jql = jira.build_my_open_jql(cfg.get("project_key") or None)
            issues = jira.search_issues(cfg, jql, max_results=body.max_results)
            project_name = "My open issues"
        elif mode == "project" or cfg.get("project_key"):
            key = cfg.get("project_key")
            if not key:
                raise jira.JiraConfigError("project_key is required for project mode")
            jql = jira.build_project_jql(key)
            issues = jira.search_issues(cfg, jql, max_results=body.max_results)
            project_name = key
        else:
            raise jira.JiraConfigError(
                "Provide jql, or set mode to board/today/my_open/project "
                "(and board_id or project_key as needed)."
            )

        data = jira.issues_to_jira_data(
            issues, project_name=project_name, base_url=cfg.get("base_url") or ""
        )
        data["jql"] = body.jql
        data["mode"] = mode or ("jql" if body.jql else "board")
        session = session_store.get_session(session_id) or {}
        data["logged_seconds"] = session_store.worklog_totals_by_issue(
            session.get("account_id")
        )
        return data
    except Exception as exc:
        _handle_jira_errors(exc)


@app.post("/jira/board")
def jira_board(
    body: JiraFetchRequest,
    session_id: Optional[str] = Header(None, alias=SESSION_HEADER),
):
    body.mode = body.mode or "board"
    return jira_issues(body, session_id=session_id)


@app.post("/jira/today")
def jira_today(
    body: JiraFetchRequest,
    session_id: Optional[str] = Header(None, alias=SESSION_HEADER),
):
    body.mode = "today"
    return jira_issues(body, session_id=session_id)


@app.get("/jira/issues/{issue_key}/transitions")
def jira_issue_transitions(
    issue_key: str,
    session_id: Optional[str] = Header(None, alias=SESSION_HEADER),
):
    try:
        cfg = _cfg(None, session_id=session_id)
        return {"issue_key": issue_key, "transitions": jira.get_transitions(cfg, issue_key)}
    except Exception as exc:
        _handle_jira_errors(exc)


@app.post("/jira/issues/{issue_key}/transition")
def jira_issue_transition(
    issue_key: str,
    body: TransitionRequest,
    session_id: Optional[str] = Header(None, alias=SESSION_HEADER),
):
    """Move a Jira issue to a target status (synced board columns)."""
    try:
        cfg = _cfg(None, session_id=session_id)
        return jira.transition_issue(
            cfg,
            issue_key,
            target_status=body.target_status,
            transition_id=body.transition_id,
        )
    except Exception as exc:
        _handle_jira_errors(exc)


# ── Work timer / worklogs ───────────────────────────────────────────────────


@app.post("/jira/worklog")
def jira_worklog(
    body: WorklogRequest,
    session_id: Optional[str] = Header(None, alias=SESSION_HEADER),
):
    """
    Stop timer → save locally, optionally push worklog to Jira issue.
    """
    try:
        session = session_store.get_session(session_id) or {}
        local = session_store.append_worklog(
            {
                "issue_key": body.issue_key,
                "seconds": body.seconds,
                "started_at": body.started_at,
                "ended_at": body.ended_at,
                "comment": body.comment,
                "pushed_to_jira": False,
                "jira_worklog_id": None,
                "account_id": session.get("account_id"),
                "session_id": session_id,
            }
        )

        jira_result = None
        if body.push_to_jira:
            cfg = _cfg(None, session_id=session_id)
            jira_result = jira.add_worklog(
                cfg,
                body.issue_key,
                seconds=body.seconds,
                started_iso=body.started_at,
                comment=body.comment,
            )
            local = (
                session_store.update_worklog(
                    local["id"],
                    {
                        "pushed_to_jira": True,
                        "jira_worklog_id": (jira_result or {}).get("id"),
                    },
                )
                or local
            )

        return {
            "ok": True,
            "local": local,
            "jira": jira_result,
            "totals": session_store.worklog_totals_by_issue(session.get("account_id")),
        }
    except Exception as exc:
        _handle_jira_errors(exc)


@app.get("/jira/worklogs")
def jira_worklogs(
    issue_key: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    session_id: Optional[str] = Header(None, alias=SESSION_HEADER),
):
    session = session_store.get_session(session_id) or {}
    account_id = session.get("account_id")
    return {
        "worklogs": session_store.list_worklogs(
            issue_key=issue_key, account_id=account_id, limit=limit
        ),
        "totals": session_store.worklog_totals_by_issue(account_id),
    }


def _reload_dotenv() -> None:
    """Re-read backend/.env so recipient edits apply without a full process restart."""
    try:
        from dotenv import load_dotenv

        env_path = os.path.join(os.path.dirname(__file__), ".env")
        load_dotenv(env_path, override=True)
    except ImportError:
        pass


def _email_recipients(default_csv: str, override: Optional[list[str]]) -> list[str]:
    if override is not None:
        return [x.strip() for x in override if x and x.strip()]
    return [x.strip() for x in (default_csv or "").split(",") if x.strip()]


def _fallback_recipients(session: dict[str, Any]) -> list[str]:
    """Use signed-in Jira user email if DAILY_EMAIL_TO is not configured."""
    email = (session.get("user_email") or "").strip()
    return [email] if email else []


def _format_hhmm(seconds: int) -> str:
    # Round up to minute granularity like manual task sheets
    mins = max(0, int(seconds) + 59) // 60
    h = mins // 60
    m = mins % 60
    return f"{h}:{m:02d}"


def _parse_iso_local(value: str, tz: ZoneInfo) -> datetime:
    raw = (value or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    return dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=tz)


def _build_daily_email_payload(
    cfg: dict[str, str], session: dict[str, Any], req: DailyEmailRequest
) -> dict[str, Any]:
    _reload_dotenv()
    tz_name = os.getenv("DAILY_EMAIL_TZ") or "Asia/Kolkata"
    tz = ZoneInfo(tz_name)
    target_day = (
        datetime.strptime(req.date, "%Y-%m-%d").date()
        if req.date
        else datetime.now(tz).date()
    )
    logs = session_store.list_worklogs(
        account_id=session.get("account_id"), limit=1000
    )
    day_logs = []
    for row in logs:
        started = row.get("started_at")
        if not started:
            continue
        try:
            start_dt = _parse_iso_local(started, tz)
        except Exception:
            continue
        if start_dt.date() != target_day:
            continue
        end_raw = row.get("ended_at")
        sec_from_row = int(row.get("seconds") or 0)
        if end_raw:
            try:
                end_dt = _parse_iso_local(end_raw, tz)
            except Exception:
                end_dt = start_dt
        else:
            # If ended_at is missing, derive out-time from duration if available
            end_dt = start_dt if sec_from_row <= 0 else start_dt + timedelta(seconds=sec_from_row)
        sec = int(sec_from_row or max(0, int((end_dt - start_dt).total_seconds())))
        day_logs.append(
            {
                "issue_key": row.get("issue_key") or "",
                "start_dt": start_dt,
                "end_dt": end_dt,
                "seconds": sec,
            }
        )

    issue_keys = sorted({x["issue_key"] for x in day_logs if x["issue_key"]})
    issue_briefs = jira.get_issue_briefs(cfg, issue_keys) if issue_keys else {}

    rows: list[dict[str, Any]] = []
    total_seconds = 0
    for idx, row in enumerate(sorted(day_logs, key=lambda x: x["start_dt"]), start=1):
        key = row["issue_key"]
        brief = issue_briefs.get(key, {})
        summary = brief.get("summary") or key
        url = brief.get("url")
        project = brief.get("project_key") or (key.split("-")[0] if "-" in key else "")
        sec = int(row["seconds"])
        total_seconds += sec
        task_text = f"{summary}"
        if url:
            task_text += f"\n{url}"
        task_text += f" - {_format_hhmm(sec)}h"
        rows.append(
            {
                "sr": idx,
                "date": row["start_dt"].strftime("%d/%m/%y"),
                "in_time": row["start_dt"].strftime("%-I:%M %p"),
                "out_time": row["end_dt"].strftime("%-I:%M %p"),
                "total_time": _format_hhmm(sec),
                "project": project,
                "task": task_text,
                "task_summary": summary,
                "task_url": url,
            }
        )

    user_name = (
        session.get("user_name")
        or session.get("user_email")
        or os.getenv("DAILY_EMAIL_USER_NAME")
        or "Team Member"
    )
    subject = f"Today Task sheet : {user_name} [{target_day.strftime('%d-%m-%Y')}]"
    to_list = _email_recipients(os.getenv("DAILY_EMAIL_TO") or "", req.to)
    if not to_list:
        to_list = _fallback_recipients(session)
    cc_list = _email_recipients(os.getenv("DAILY_EMAIL_CC") or "", req.cc)

    # HTML preview in user's preferred screenshot style.
    tr_html = "".join(
        f"""
        <tr>
          <td style="border:1px solid #999;padding:6px;text-align:center;">{r['sr']}</td>
          <td style="border:1px solid #999;padding:6px;text-align:center;">{r['date']}</td>
          <td style="border:1px solid #999;padding:6px;text-align:center;">{r['in_time']}</td>
          <td style="border:1px solid #999;padding:6px;text-align:center;">{r['out_time']}</td>
          <td style="border:1px solid #999;padding:6px;text-align:center;">{r['total_time']}</td>
          <td style="border:1px solid #999;padding:6px;text-align:center;">{r['project']}</td>
          <td style="border:1px solid #999;padding:6px;white-space:pre-line;">
            {r['task_summary']}
            {f"<br><a href='{r['task_url']}'>{r['task_url']}</a>" if r['task_url'] else ""}
            <span> - {r['total_time']}h</span>
          </td>
        </tr>
        """
        for r in rows
    )
    html = f"""
    <div style="font-family:Arial,sans-serif;">
      <p><strong>GREETINGS:</strong><br/>Respected TL/PM/HR,</p>
      <table style="border-collapse:collapse;width:100%;font-size:13px;">
        <thead>
          <tr style="background:#f3c623;">
            <th style="border:1px solid #999;padding:6px;">#</th>
            <th style="border:1px solid #999;padding:6px;">Date</th>
            <th style="border:1px solid #999;padding:6px;">In-Time</th>
            <th style="border:1px solid #999;padding:6px;">Out-Time</th>
            <th style="border:1px solid #999;padding:6px;">Total Time</th>
            <th style="border:1px solid #999;padding:6px;">Project</th>
            <th style="border:1px solid #999;padding:6px;">Task</th>
          </tr>
        </thead>
        <tbody>
          {tr_html}
        </tbody>
      </table>
      <p style="margin-top:12px;"><strong>Total:</strong> {_format_hhmm(total_seconds)}h</p>
      <p style="margin-top:16px;">Regards,<br/>{user_name}</p>
    </div>
    """

    # Table only — Total/Regards live in the outer template (avoid double signature).
    table_lines = [
        "# | Date | In-Time | Out-Time | Total Time | Project | Task",
    ]
    for r in rows:
        task_text = r["task_summary"]
        if r.get("task_url"):
            task_text += f" ({r['task_url']})"
        table_lines.append(
            f"{r['sr']} | {r['date']} | {r['in_time']} | {r['out_time']} | "
            f"{r['total_time']} | {r['project']} | {task_text}"
        )
    rows_text = "\n".join(table_lines)
    default_template = (
        "GREETINGS:\n"
        "Respected TL/PM/HR,\n\n"
        "{rows}\n\n"
        "Total: {total_time}h\n\n"
        "Regards,\n"
        "{name}"
    )
    template = os.getenv("DAILY_EMAIL_TEMPLATE") or default_template
    text_body = template.format(
        name=user_name,
        date=str(target_day),
        rows=rows_text,
        total_time=_format_hhmm(total_seconds),
    )

    return {
        "date": str(target_day),
        "subject": subject,
        "to": to_list,
        "cc": cc_list,
        "rows": rows,
        "total_seconds": total_seconds,
        "total_time": _format_hhmm(total_seconds),
        "html": html,
        "text_body": text_body,
        "rows_text": rows_text,
        "user_name": user_name,
        "tz": tz_name,
    }


def _open_draft_in_mac_mail(payload: dict[str, Any]) -> None:
    """
    Open a NEW outgoing message in Apple Mail (compose with Send),
    prefilled with subject/to/cc and the current template text body.
    Also copies HTML to the system clipboard for optional rich paste (Cmd+V).
    """
    subject = str(payload.get("subject") or "")
    to_csv = ",".join(payload.get("to") or [])
    cc_csv = ",".join(payload.get("cc") or [])
    # Apple Mail compose via AppleScript is most reliable with plain text body.
    body = str(payload.get("text_body") or "")
    html = str(payload.get("html") or "")

    drafts_dir = os.path.join(os.path.dirname(__file__), "data", "mail_drafts")
    os.makedirs(drafts_dir, exist_ok=True)
    body_path = os.path.join(drafts_dir, "latest-body.txt")
    with open(body_path, "w", encoding="utf-8") as f:
        f.write(body)

    # Put formatted HTML on clipboard so user can Cmd+V for rich table if wanted.
    if html.strip():
        try:
            html_hex = html.encode("utf-8").hex()
            if len(html_hex) < 180_000:
                subprocess.run(
                    ["osascript", "-e", f"set the clipboard to «data HTML{html_hex}»"],
                    check=False,
                    timeout=10,
                    capture_output=True,
                )
            else:
                subprocess.run(
                    ["pbcopy"],
                    input=body,
                    text=True,
                    check=False,
                    timeout=10,
                )
        except Exception:
            pass

    script = r"""
on splitCSV(theText)
    if theText is "" then return {}
    set oldDelims to AppleScript's text item delimiters
    set AppleScript's text item delimiters to ","
    set parts to text items of theText
    set AppleScript's text item delimiters to oldDelims
    return parts
end splitCSV

on run argv
    set theSubject to item 1 of argv
    set toCSV to item 2 of argv
    set ccCSV to item 3 of argv
    set bodyPath to item 4 of argv
    set theBody to do shell script "cat " & quoted form of bodyPath

    tell application "Mail"
        activate
        set newMessage to make new outgoing message with properties {subject:theSubject, content:theBody & return & return, visible:true}
        tell newMessage
            repeat with addr in my splitCSV(toCSV)
                set a to (addr as text)
                if a is not "" then
                    make new to recipient at end of to recipients with properties {address:a}
                end if
            end repeat
            repeat with addr in my splitCSV(ccCSV)
                set a to (addr as text)
                if a is not "" then
                    make new cc recipient at end of cc recipients with properties {address:a}
                end if
            end repeat
        end tell
    end tell
end run
"""

    subprocess.run(
        ["osascript", "-", subject, to_csv, cc_csv, body_path],
        input=script,
        text=True,
        check=True,
        timeout=20,
    )


def _open_html_eml_in_mac_mail(payload: dict[str, Any], session: dict[str, Any]) -> str:
    """
    Build an HTML .eml draft and open it in macOS Mail.
    This preserves full template formatting unlike plain AppleScript content.
    Returns file path for debugging.
    """
    msg = EmailMessage(policy=SMTP)
    msg["Subject"] = str(payload.get("subject") or "")
    # Mark as draft so Apple Mail opens compose window with Send button.
    msg["X-Unsent"] = "1"
    from_addr = (
        os.getenv("DAILY_EMAIL_FROM")
        or (session.get("user_email") or "").strip()
        or "no-reply@localhost"
    )
    msg["From"] = from_addr
    to_list = payload.get("to") or []
    cc_list = payload.get("cc") or []
    if to_list:
        msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)

    text_body = str(payload.get("text_body") or "")
    html_body = str(payload.get("html") or "")
    msg.set_content(text_body or "Daily task sheet")
    if html_body.strip():
        msg.add_alternative(html_body, subtype="html")

    drafts_dir = os.path.join(os.path.dirname(__file__), "data", "mail_drafts")
    os.makedirs(drafts_dir, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", suffix=".eml", prefix="daily-task-", dir=drafts_dir, delete=False
    ) as f:
        f.write(msg.as_bytes())
        eml_path = f.name

    subprocess.run(["open", "-a", "Mail", eml_path], check=True, timeout=20)
    return eml_path


@app.post("/reports/daily-email/preview")
def daily_email_preview(
    body: DailyEmailRequest = DailyEmailRequest(),
    session_id: Optional[str] = Header(None, alias=SESSION_HEADER),
):
    try:
        cfg = _cfg(None, session_id=session_id)
        session = session_store.get_session(session_id) or {}
        payload = _build_daily_email_payload(cfg, session, body)
        smtp_ready = bool(
            os.getenv("SMTP_HOST")
            and os.getenv("SMTP_PORT")
            and os.getenv("SMTP_USER")
            and os.getenv("SMTP_PASS")
            and os.getenv("SMTP_FROM")
        )
        return {"ok": True, "smtp_ready": smtp_ready, **payload}
    except Exception as exc:
        _handle_jira_errors(exc)


@app.post("/reports/daily-email/send")
def daily_email_send(
    body: DailyEmailRequest = DailyEmailRequest(),
    session_id: Optional[str] = Header(None, alias=SESSION_HEADER),
):
    try:
        cfg = _cfg(None, session_id=session_id)
        session = session_store.get_session(session_id) or {}
        payload = _build_daily_email_payload(cfg, session, body)

        host = os.getenv("SMTP_HOST") or ""
        port = int(os.getenv("SMTP_PORT") or "0")
        user = os.getenv("SMTP_USER") or ""
        passwd = os.getenv("SMTP_PASS") or ""
        sender = os.getenv("SMTP_FROM") or user
        use_tls = (os.getenv("SMTP_TLS") or "1").lower() in ("1", "true", "yes")
        if not (host and port and user and passwd and sender):
            raise jira.JiraConfigError(
                "Email is not configured. Set SMTP_HOST, SMTP_PORT, SMTP_USER, "
                "SMTP_PASS, SMTP_FROM in backend/.env."
            )
        if not payload["to"]:
            raise jira.JiraConfigError("No recipients in DAILY_EMAIL_TO or request 'to'.")

        msg = EmailMessage()
        msg["Subject"] = payload["subject"]
        msg["From"] = sender
        msg["To"] = ", ".join(payload["to"])
        if payload["cc"]:
            msg["Cc"] = ", ".join(payload["cc"])
        msg.set_content(
            f"Please find today's task sheet attached in HTML format.\n"
            f"Total time: {payload['total_time']}h\n"
        )
        msg.add_alternative(payload["html"], subtype="html")

        recipients = payload["to"] + payload["cc"]
        with smtplib.SMTP(host, port, timeout=30) as server:
            if use_tls:
                server.starttls()
            server.login(user, passwd)
            server.send_message(msg, from_addr=sender, to_addrs=recipients)

        return {
            "ok": True,
            "sent": True,
            "subject": payload["subject"],
            "to": payload["to"],
            "cc": payload["cc"],
            "rows": len(payload["rows"]),
            "total_time": payload["total_time"],
        }
    except Exception as exc:
        _handle_jira_errors(exc)


@app.post("/reports/daily-email/open-mail-app")
def daily_email_open_mail_app(
    body: DailyEmailRequest = DailyEmailRequest(),
    session_id: Optional[str] = Header(None, alias=SESSION_HEADER),
):
    """
    Create a new draft directly in macOS Mail (no SMTP needed).
    """
    try:
        cfg = _cfg(None, session_id=session_id)
        session = session_store.get_session(session_id) or {}
        payload = _build_daily_email_payload(cfg, session, body)
        if not payload.get("to"):
            raise jira.JiraConfigError("No recipients in DAILY_EMAIL_TO or request 'to'.")
        _open_draft_in_mac_mail(payload)
        return {
            "ok": True,
            "opened": True,
            "subject": payload.get("subject"),
            "to": payload.get("to"),
            "cc": payload.get("cc"),
        }
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(
            status_code=504, detail="Timed out while opening Apple Mail draft."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not open Apple Mail draft: {exc}",
        ) from exc
    except Exception as exc:
        _handle_jira_errors(exc)


@app.post("/reports/daily-email/open-mail-html")
def daily_email_open_mail_html(
    body: DailyEmailRequest = DailyEmailRequest(),
    session_id: Optional[str] = Header(None, alias=SESSION_HEADER),
):
    """
    Open an HTML .eml draft in macOS Mail preserving table/template formatting.
    """
    try:
        cfg = _cfg(None, session_id=session_id)
        session = session_store.get_session(session_id) or {}
        payload = _build_daily_email_payload(cfg, session, body)
        if not payload.get("to"):
            raise jira.JiraConfigError("No recipients in DAILY_EMAIL_TO or request 'to'.")
        eml_path = _open_html_eml_in_mac_mail(payload, session)
        return {
            "ok": True,
            "opened": True,
            "subject": payload.get("subject"),
            "to": payload.get("to"),
            "cc": payload.get("cc"),
            "eml_path": eml_path,
        }
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(
            status_code=504, detail="Timed out while opening Apple Mail HTML draft."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not open Apple Mail HTML draft: {exc}",
        ) from exc
    except Exception as exc:
        _handle_jira_errors(exc)


def _handle_github_errors(exc: Exception) -> None:
    if isinstance(exc, gh.GitHubConfigError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, gh.GitHubApiError):
        raise HTTPException(
            status_code=exc.status_code or 502, detail=str(exc)
        ) from exc
    if isinstance(exc, github_oauth.GitHubOAuthError):
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if isinstance(exc, local_git.LocalGitError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc


def _github_creds_from_request(
    body: Any = None,
    gh_session_id: str | None = None,
) -> dict[str, str | None]:
    """Prefer OAuth session token; fall back to PAT fields on the body."""
    token = None
    username = None
    display_name = None
    session = gh_sessions.get_session(gh_session_id)
    if session:
        token = session.get("access_token")
        username = session.get("username")
        display_name = session.get("user_name") or username
    if body is not None:
        token = (getattr(body, "github_token", None) or "").strip() or token
        username = (getattr(body, "github_username", None) or "").strip() or username
        display_name = (
            (getattr(body, "display_name", None) or "").strip()
            or display_name
            or username
        )
    return {
        "token": token,
        "username": username,
        "display_name": display_name,
        "repos": (getattr(body, "github_repos", None) if body is not None else None),
        "author_email": (
            getattr(body, "author_email", None) if body is not None else None
        ),
        "date": getattr(body, "date", None) if body is not None else None,
        "to": getattr(body, "to", None) if body is not None else None,
        "cc": getattr(body, "cc", None) if body is not None else None,
    }


@app.get("/auth/github/setup")
def github_oauth_setup():
    return github_oauth.setup_checklist()


@app.get("/auth/github/login")
def github_oauth_login(request: Request):
    """Redirect browser to GitHub authorize screen (no PAT needed)."""
    try:
        sid = request.query_params.get("sid") or None
        url, _sid = github_oauth.build_authorize_url(sid)
        return RedirectResponse(url)
    except Exception as exc:
        _handle_github_errors(exc)


@app.get("/auth/github/callback")
def github_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    frontend = github_oauth.oauth_env()["frontend_url"]
    if error:
        msg = error_description or error or "GitHub OAuth denied"
        return RedirectResponse(f"{frontend}?tab=github&github_error={quote(msg)}")
    if not code:
        return RedirectResponse(f"{frontend}?tab=github&github_error=missing_code")
    try:
        _session, sid = github_oauth.complete_login(code, state)
        return RedirectResponse(
            f"{frontend}?tab=github&github=connected&gh_sid={quote(sid)}"
        )
    except Exception as exc:
        return RedirectResponse(
            f"{frontend}?tab=github&github_error={quote(str(exc))}"
        )


@app.get("/auth/github/status")
def github_oauth_status(
    session_id: Optional[str] = Header(None, alias=GITHUB_SESSION_HEADER),
):
    setup = github_oauth.setup_checklist()
    session = gh_sessions.get_session(session_id)
    if not session:
        return {
            "connected": False,
            "oauth_configured": github_oauth.oauth_configured(),
            "redirect_uri": setup["redirect_uri"],
            "setup": setup,
        }
    return {
        "connected": True,
        "oauth_configured": True,
        "session_id": session_id,
        "auth_type": session.get("auth_type") or "oauth",
        "username": session.get("username"),
        "user_name": session.get("user_name"),
        "user_email": session.get("user_email"),
        "avatar_url": session.get("avatar_url"),
        "html_url": session.get("html_url"),
        "redirect_uri": setup["redirect_uri"],
        "setup": setup,
    }


@app.post("/auth/github/logout")
def github_oauth_logout(
    session_id: Optional[str] = Header(None, alias=GITHUB_SESSION_HEADER),
):
    gh_sessions.clear_session(session_id)
    return {"ok": True, "connected": False}


@app.get("/reports/github-daily/status")
def github_daily_status():
    """OAuth / optional env status (no secrets)."""
    _reload_dotenv()
    setup = github_oauth.setup_checklist()
    return {
        "ok": True,
        "oauth_configured": github_oauth.oauth_configured(),
        **gh.github_status(),
        "setup": setup,
    }


@app.post("/reports/github-daily/local/inspect")
def github_daily_local_inspect(body: GitHubLocalRepoRequest):
    """Validate a local git repo path and return current branch (GitHub Daily only)."""
    try:
        info = local_git.inspect_repo(body.repo_path)
        if body.branch and body.branch.strip():
            info["branch"] = body.branch.strip()
        return {"ok": True, **info}
    except Exception as exc:
        _handle_github_errors(exc)


@app.get("/reports/github-daily/local/timer")
def github_daily_local_timer_status(
    owner_key: str = Query(..., min_length=1),
    date: Optional[str] = Query(None, description="YYYY-MM-DD"),
):
    """Active local timer + saved sessions for the day."""
    _reload_dotenv()
    tz_name = os.getenv("DAILY_EMAIL_TZ") or "Asia/Kolkata"
    active = gh_local.get_active(owner_key)
    day = date
    if not day:
        day = datetime.now(ZoneInfo(tz_name)).date().isoformat()
    sessions = gh_local.list_sessions(
        owner_key=owner_key, day=day, tz_name=tz_name
    )
    return {
        "ok": True,
        "active": active,
        "sessions": sessions,
        "date": day,
        "tz": tz_name,
    }


@app.post("/reports/github-daily/local/timer/start")
def github_daily_local_timer_start(body: GitHubLocalTimerStartRequest):
    """
    Start a local-branch work timer (GitHub Daily only).
    Does not touch Jira timers/worklogs.
    """
    try:
        if gh_local.get_active(body.owner_key):
            raise local_git.LocalGitError(
                "A local timer is already running. Stop it before starting another."
            )
        info = local_git.inspect_repo(body.repo_path)
        branch = (body.branch or "").strip() or info["branch"]
        # Create/checkout branch locally if missing (published to GitHub on first push).
        branch_meta = local_git.ensure_branch(info["repo_path"], branch)
        started = local_git.utc_now_iso()
        timer = {
            "owner_key": body.owner_key,
            "repo_path": info["repo_path"],
            "branch": branch_meta.get("branch") or branch,
            "project": info["project"],
            "full_name": info["full_name"],
            "remote": info.get("remote") or "",
            "started_at": started,
            "display_name": (body.display_name or "").strip() or None,
            "branch_created": bool(branch_meta.get("created")),
        }
        gh_local.set_active(body.owner_key, timer)
        return {"ok": True, "active": timer}
    except Exception as exc:
        _handle_github_errors(exc)


@app.post("/reports/github-daily/local/timer/stop")
def github_daily_local_timer_stop(
    body: GitHubLocalTimerStopRequest,
    x_github_session: str | None = Header(None, alias=GITHUB_SESSION_HEADER),
):
    """
    Stop local timer, collect commits on that branch since start, save on our side.
    Optionally create a commit (+ push to GitHub) first if commit_message is provided.
    """
    try:
        active = gh_local.get_active(body.owner_key)
        if not active:
            raise local_git.LocalGitError("No active local timer to stop.")

        created = None
        pushed = None
        msg = (body.commit_message or "").strip()
        if msg:
            created = local_git.create_commit(
                active["repo_path"],
                message=msg,
                add_all=body.add_all,
                branch=active.get("branch"),
            )
            if body.push:
                creds = _github_creds_from_request(body, x_github_session)
                try:
                    pushed = local_git.push_to_remote(
                        active["repo_path"],
                        branch=active.get("branch"),
                        token=creds.get("token"),
                    )
                except local_git.LocalGitError as push_exc:
                    raise local_git.LocalGitError(
                        f"Committed locally ({created.get('short_sha')}), "
                        f"but GitHub push failed: {push_exc}"
                    ) from push_exc

        ended = local_git.utc_now_iso()
        started = active.get("started_at") or ended
        try:
            start_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(ended.replace("Z", "+00:00"))
            seconds = max(1, int((end_dt - start_dt).total_seconds()))
        except ValueError:
            seconds = 60

        commits = local_git.list_commits_since(
            active["repo_path"],
            branch=active.get("branch") or "HEAD",
            since_iso=started,
            until_iso=ended,
            author=(body.author or "").strip() or None,
        )
        saved = gh_local.append_session(
            {
                **active,
                "ended_at": ended,
                "seconds": seconds,
                "commits": commits,
                "commit_count": len(commits),
                "created_commit": created,
                "pushed": pushed,
            }
        )
        gh_local.set_active(body.owner_key, None)
        return {
            "ok": True,
            "session": saved,
            "active": None,
            "created_commit": created,
            "pushed": pushed,
        }
    except Exception as exc:
        _handle_github_errors(exc)


@app.post("/reports/github-daily/local/commit")
def github_daily_local_commit(
    body: GitHubLocalCommitRequest,
    x_github_session: str | None = Header(None, alias=GITHUB_SESSION_HEADER),
):
    """
    Create a local git commit from the app and optionally push to GitHub.
    Uses active timer repo/branch when owner_key is set.
    """
    try:
        repo_path = (body.repo_path or "").strip()
        branch = (body.branch or "").strip() or None
        if body.owner_key:
            active = gh_local.get_active(body.owner_key)
            if active:
                repo_path = repo_path or active.get("repo_path") or ""
                branch = branch or active.get("branch")
        if not repo_path:
            raise local_git.LocalGitError("repo_path is required (or start a timer first).")
        created = local_git.create_commit(
            repo_path,
            message=body.message,
            add_all=body.add_all,
            branch=branch,
        )
        pushed = None
        if body.push:
            creds = _github_creds_from_request(body, x_github_session)
            try:
                pushed = local_git.push_to_remote(
                    repo_path,
                    branch=created.get("branch") or branch,
                    token=creds.get("token"),
                )
            except local_git.LocalGitError as push_exc:
                raise local_git.LocalGitError(
                    f"Committed locally ({created.get('short_sha')}), "
                    f"but GitHub push failed: {push_exc}"
                ) from push_exc
        status = local_git.status_summary(repo_path)
        return {
            "ok": True,
            "commit": created,
            "pushed": pushed,
            "status": status,
        }
    except Exception as exc:
        _handle_github_errors(exc)


@app.post("/reports/github-daily/local/push")
def github_daily_local_push(
    body: GitHubLocalPushRequest,
    x_github_session: str | None = Header(None, alias=GITHUB_SESSION_HEADER),
):
    """Push the current local branch to GitHub (origin)."""
    try:
        repo_path = (body.repo_path or "").strip()
        branch = (body.branch or "").strip() or None
        if body.owner_key:
            active = gh_local.get_active(body.owner_key)
            if active:
                repo_path = repo_path or active.get("repo_path") or ""
                branch = branch or active.get("branch")
        if not repo_path:
            raise local_git.LocalGitError("repo_path is required (or start a timer first).")
        creds = _github_creds_from_request(body, x_github_session)
        pushed = local_git.push_to_remote(
            repo_path,
            branch=branch,
            token=creds.get("token"),
        )
        return {"ok": True, "pushed": pushed}
    except Exception as exc:
        _handle_github_errors(exc)


@app.get("/reports/github-daily/local/status")
def github_daily_local_status(
    repo_path: str = Query(...),
):
    """Working tree status for the local repo."""
    try:
        info = local_git.inspect_repo(repo_path)
        status = local_git.status_summary(repo_path)
        return {"ok": True, **info, **status}
    except Exception as exc:
        _handle_github_errors(exc)


@app.post("/reports/github-daily/local/timer/discard")
def github_daily_local_timer_discard(body: GitHubLocalTimerStopRequest):
    """Discard active local timer without saving."""
    gh_local.set_active(body.owner_key, None)
    return {"ok": True, "active": None}


@app.post("/reports/github-daily-email/preview-local")
def github_daily_email_preview_local(body: GitHubLocalTimerPreviewRequest):
    """Preview Today Task sheet from saved local timer sessions only."""
    try:
        _reload_dotenv()
        tz_name = os.getenv("DAILY_EMAIL_TZ") or "Asia/Kolkata"
        day = body.date or datetime.now(ZoneInfo(tz_name)).date().isoformat()
        sessions = gh_local.list_sessions(
            owner_key=body.owner_key, day=day, tz_name=tz_name
        )
        payload = github_daily.build_github_local_timer_email_payload(
            sessions,
            req_date=day,
            req_to=body.to,
            req_cc=body.cc,
            fallback_name=body.display_name,
        )
        if not payload.get("to"):
            raise gh.GitHubConfigError(
                "No recipients. Set DAILY_EMAIL_TO in backend/.env."
            )
        return {"ok": True, **payload}
    except Exception as exc:
        _handle_github_errors(exc)


@app.post("/reports/github-daily-email/open-mail-local")
def github_daily_email_open_mail_local(body: GitHubLocalTimerPreviewRequest):
    """Open Mac Mail with sheet built from local timer sessions."""
    try:
        _reload_dotenv()
        tz_name = os.getenv("DAILY_EMAIL_TZ") or "Asia/Kolkata"
        day = body.date or datetime.now(ZoneInfo(tz_name)).date().isoformat()
        sessions = gh_local.list_sessions(
            owner_key=body.owner_key, day=day, tz_name=tz_name
        )
        payload = github_daily.build_github_local_timer_email_payload(
            sessions,
            req_date=day,
            req_to=body.to,
            req_cc=body.cc,
            fallback_name=body.display_name,
        )
        if not payload.get("to"):
            raise gh.GitHubConfigError(
                "No recipients. Set DAILY_EMAIL_TO in backend/.env."
            )
        _open_draft_in_mac_mail(payload)
        return {
            "ok": True,
            "opened": True,
            "subject": payload.get("subject"),
            "to": payload.get("to"),
            "session_count": payload.get("session_count"),
        }
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(
            status_code=504, detail="Timed out while opening Apple Mail draft."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not open Apple Mail draft: {exc}",
        ) from exc
    except Exception as exc:
        _handle_github_errors(exc)


@app.post("/reports/github-daily/connect")
def github_daily_connect(body: GitHubConnectRequest):
    """
    Optional PAT fallback when OAuth is not used.
    Prefer /auth/github/login for normal users.
    """
    try:
        token = (body.github_token or "").strip()
        if not token:
            raise gh.GitHubConfigError("GitHub token is required for PAT connect.")
        profile = gh.verify_token(token)
        login = profile["login"]
        requested = (body.github_username or "").strip()
        if requested and requested.lower() != login.lower():
            raise gh.GitHubConfigError(
                f"Token belongs to '{login}', not '{requested}'."
            )
        # Persist as a short-lived server session so UI can use X-Github-Session
        sid = gh_sessions.new_session_id()
        gh_sessions.save_session(
            sid,
            {
                "auth_type": "pat",
                "access_token": token,
                "username": login,
                "user_name": profile.get("name") or login,
                "user_email": profile.get("email") or "",
                "avatar_url": profile.get("avatar_url"),
                "html_url": profile.get("html_url"),
            },
        )
        projects = gh.list_accessible_repos(token)
        return {
            "ok": True,
            "connected": True,
            "session_id": sid,
            "username": login,
            "name": profile.get("name") or login,
            "avatar_url": profile.get("avatar_url"),
            "html_url": profile.get("html_url"),
            "projects": projects,
            "project_count": len(projects),
        }
    except Exception as exc:
        _handle_github_errors(exc)


@app.post("/reports/github-daily/projects")
def github_daily_projects(
    body: GitHubConnectRequest = GitHubConnectRequest(),
    session_id: Optional[str] = Header(None, alias=GITHUB_SESSION_HEADER),
):
    """List accessible GitHub projects for the connected OAuth/PAT session."""
    try:
        creds = _github_creds_from_request(body, session_id)
        token = creds["token"]
        if not token:
            raise gh.GitHubConfigError(
                "Connect with GitHub first (OAuth), or provide a PAT."
            )
        projects = gh.list_accessible_repos(str(token))
        orgs = gh.list_user_orgs(str(token))
        return {
            "ok": True,
            "projects": projects,
            "project_count": len(projects),
            "orgs": orgs,
            "org_count": len(orgs),
            "hint": (
                "Showing only repos assigned to you (owner / collaborator / write access). "
                "Missing a company repo? Disconnect → Connect with GitHub → Grant your org, "
                "then Refresh. Org owners may need to approve this OAuth App under "
                "Third-party access."
                if not orgs
                else None
            ),
        }
    except Exception as exc:
        _handle_github_errors(exc)


@app.post("/reports/github-daily-email/preview")
def github_daily_email_preview(
    body: GitHubDailyRequest = GitHubDailyRequest(),
    session_id: Optional[str] = Header(None, alias=GITHUB_SESSION_HEADER),
):
    """Commits on connected projects — prefers stored local timer In/Out times."""
    try:
        creds = _github_creds_from_request(body, session_id)
        repos = (creds["repos"] or body.github_repos or "").strip()
        if not repos:
            raise gh.GitHubConfigError(
                "Connect GitHub, then select at least one project before preview."
            )
        if not creds["token"]:
            raise gh.GitHubConfigError("Connect with GitHub first.")
        _reload_dotenv()
        tz_name = os.getenv("DAILY_EMAIL_TZ") or "Asia/Kolkata"
        day = body.date or datetime.now(ZoneInfo(tz_name)).date().isoformat()
        local_sessions = gh_local.list_sessions(day=day, tz_name=tz_name)
        payload = github_daily.build_github_daily_email_payload(
            req_date=body.date,
            req_to=body.to,
            req_cc=body.cc,
            fallback_name=creds["display_name"] or creds["username"],
            token=creds["token"],
            username=creds["username"],
            repos_csv=repos,
            author_email=body.author_email,
            local_sessions=local_sessions,
        )
        if not payload.get("to"):
            raise gh.GitHubConfigError(
                "No recipients. Set DAILY_EMAIL_TO in backend/.env."
            )
        return {"ok": True, **payload}
    except Exception as exc:
        _handle_github_errors(exc)


@app.post("/reports/github-daily-email/open-mail-app")
def github_daily_email_open_mail_app(
    body: GitHubDailyRequest = GitHubDailyRequest(),
    session_id: Optional[str] = Header(None, alias=GITHUB_SESSION_HEADER),
):
    """Open Mac Mail compose with GitHub-based sheet."""
    try:
        creds = _github_creds_from_request(body, session_id)
        repos = (creds["repos"] or body.github_repos or "").strip()
        if not repos:
            raise gh.GitHubConfigError(
                "Select at least one connected GitHub project before opening mail."
            )
        if not creds["token"]:
            raise gh.GitHubConfigError("Connect with GitHub first.")
        _reload_dotenv()
        tz_name = os.getenv("DAILY_EMAIL_TZ") or "Asia/Kolkata"
        day = body.date or datetime.now(ZoneInfo(tz_name)).date().isoformat()
        local_sessions = gh_local.list_sessions(day=day, tz_name=tz_name)
        payload = github_daily.build_github_daily_email_payload(
            req_date=body.date,
            req_to=body.to,
            req_cc=body.cc,
            fallback_name=creds["display_name"] or creds["username"],
            token=creds["token"],
            username=creds["username"],
            repos_csv=repos,
            author_email=body.author_email,
            local_sessions=local_sessions,
        )
        if not payload.get("to"):
            raise gh.GitHubConfigError(
                "No recipients. Set DAILY_EMAIL_TO in backend/.env."
            )
        _open_draft_in_mac_mail(payload)
        return {
            "ok": True,
            "opened": True,
            "subject": payload.get("subject"),
            "to": payload.get("to"),
            "cc": payload.get("cc"),
            "commit_count": payload.get("commit_count"),
            "session_count": payload.get("session_count"),
            "used_local_timers": payload.get("used_local_timers"),
        }
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(
            status_code=504, detail="Timed out while opening Apple Mail draft."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not open Apple Mail draft: {exc}",
        ) from exc
    except Exception as exc:
        _handle_github_errors(exc)
