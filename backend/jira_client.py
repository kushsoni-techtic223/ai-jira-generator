"""Jira Cloud REST client — fetch board/project issues and map to app ticket shape."""

from __future__ import annotations

import base64
import os
import re
from typing import Any

import requests

from http_client import external_session

PRIORITY_MAP = {
    "highest": "Highest",
    "blocker": "Highest",
    "critical": "Highest",
    "high": "High",
    "medium": "Medium",
    "major": "Medium",
    "low": "Low",
    "minor": "Low",
    "trivial": "Low",
    "lowest": "Low",
}

ISSUE_FIELDS = [
    "summary",
    "description",
    "status",
    "priority",
    "labels",
    "components",
    "assignee",
    "reporter",
    "issuetype",
    "parent",
    "subtasks",
    "created",
    "updated",
    "duedate",
    "project",
    "comment",
]

ISSUE_BRIEF_FIELDS = ["summary", "project"]


class JiraConfigError(Exception):
    pass


class JiraApiError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _env_config() -> dict[str, str]:
    return {
        "base_url": (os.getenv("JIRA_BASE_URL") or "").rstrip("/"),
        "email": os.getenv("JIRA_EMAIL") or "",
        "api_token": os.getenv("JIRA_API_TOKEN") or "",
        "project_key": os.getenv("JIRA_PROJECT_KEY") or "",
        "board_id": os.getenv("JIRA_BOARD_ID") or "",
        "auth_type": "",
        "access_token": "",
        "cloud_id": "",
    }


def resolve_config(overrides: dict[str, Any] | None = None) -> dict[str, str]:
    cfg = _env_config()
    if overrides:
        for key in (
            "base_url",
            "email",
            "api_token",
            "project_key",
            "board_id",
            "auth_type",
            "access_token",
            "cloud_id",
        ):
            val = overrides.get(key)
            if val is not None and str(val).strip():
                cfg[key] = (
                    str(val).strip().rstrip("/")
                    if key == "base_url"
                    else str(val).strip()
                )
    return cfg


def has_oauth(cfg: dict[str, str]) -> bool:
    return bool(cfg.get("access_token") and cfg.get("cloud_id"))


def has_basic(cfg: dict[str, str]) -> bool:
    return bool(cfg.get("base_url") and cfg.get("email") and cfg.get("api_token"))


def has_credentials(cfg: dict[str, str]) -> bool:
    return has_oauth(cfg) or has_basic(cfg)


def _auth_header(email: str, api_token: str) -> str:
    token = base64.b64encode(f"{email}:{api_token}".encode()).decode()
    return f"Basic {token}"


def _api_base(cfg: dict[str, str]) -> str:
    if has_oauth(cfg):
        return f"https://api.atlassian.com/ex/jira/{cfg['cloud_id']}"
    return (cfg.get("base_url") or "").rstrip("/")


def _session(cfg: dict[str, str]) -> requests.Session:
    if not has_credentials(cfg):
        raise JiraConfigError(
            "Jira is not configured. Connect with OAuth, or set JIRA_BASE_URL, "
            "JIRA_EMAIL, and JIRA_API_TOKEN in the backend .env."
        )
    s = external_session()
    if has_oauth(cfg):
        auth = f"Bearer {cfg['access_token']}"
    else:
        auth = _auth_header(cfg["email"], cfg["api_token"])
    s.headers.update(
        {
            "Authorization": auth,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
    )
    return s


def _request(
    cfg: dict[str, str],
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json_body: dict | None = None,
    timeout: int = 60,
) -> Any:
    base = _api_base(cfg)
    if not base:
        raise JiraConfigError("Missing Jira API base URL / cloud id.")
    url = f"{base}{path}"
    try:
        resp = _session(cfg).request(
            method, url, params=params, json=json_body, timeout=timeout
        )
    except requests.RequestException as exc:
        raise JiraApiError(f"Could not reach Jira: {exc}") from exc

    if resp.status_code == 401:
        raise JiraApiError(
            "Jira authentication failed. Reconnect with OAuth or check API token.",
            401,
        )
    if resp.status_code == 403:
        raise JiraApiError("Jira access denied for this account/token.", 403)
    if resp.status_code >= 400:
        detail = resp.text[:500]
        try:
            detail = (
                resp.json().get("errorMessages")
                or resp.json().get("message")
                or detail
            )
        except Exception:
            pass
        raise JiraApiError(
            f"Jira API error ({resp.status_code}): {detail}", resp.status_code
        )

    if resp.status_code == 204 or not resp.content:
        return None
    return resp.json()


def adf_to_text(node: Any) -> str:
    """Flatten Atlassian Document Format (or plain string) to readable text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node.strip()
    if isinstance(node, list):
        return "\n".join(filter(None, (adf_to_text(n) for n in node))).strip()
    if not isinstance(node, dict):
        return str(node).strip()

    ntype = node.get("type")
    text = node.get("text") or ""
    content = node.get("content") or []
    inner = adf_to_text(content)

    if ntype == "hardBreak":
        return "\n"
    if ntype in ("paragraph", "heading", "blockquote"):
        return (inner or text) + "\n"
    if ntype in ("bulletList", "orderedList"):
        return inner + ("\n" if inner else "")
    if ntype == "listItem":
        line = inner.strip().replace("\n", " ")
        return f"- {line}\n" if line else ""
    if ntype == "codeBlock":
        return f"```\n{inner.strip()}\n```\n"
    if ntype == "rule":
        return "---\n"
    return (inner or text).strip()


def map_priority(name: str | None) -> str:
    if not name:
        return "Medium"
    return PRIORITY_MAP.get(name.strip().lower(), "Medium")


def map_status(fields: dict) -> str:
    status = fields.get("status") or {}
    category = ((status.get("statusCategory") or {}).get("key") or "").lower()
    name = (status.get("name") or "").lower()
    if category == "done" or name in ("done", "closed", "resolved", "complete", "completed"):
        return "done"
    if category == "indeterminate" or "progress" in name or name in ("in review", "review"):
        return "in_progress"
    return "todo"


def _subtask_titles(fields: dict) -> list[str]:
    titles: list[str] = []
    for st in fields.get("subtasks") or []:
        summary = (st.get("fields") or {}).get("summary")
        if summary:
            titles.append(summary)
    return titles


def _acceptance_from_description(description: str) -> list[str]:
    lines = [ln.strip(" -*\t") for ln in description.splitlines() if ln.strip()]
    ac: list[str] = []
    capture = False
    for ln in lines:
        lower = ln.lower()
        if "acceptance" in lower and "criteria" in lower:
            capture = True
            continue
        if capture:
            if lower.startswith(("description", "notes", "tasks", "subtasks")):
                break
            if ln:
                ac.append(ln)
    return ac[:20]


def issue_to_story(issue: dict) -> dict[str, Any]:
    fields = issue.get("fields") or {}
    key = issue.get("key") or "UNKNOWN"
    description = adf_to_text(fields.get("description"))
    components = [c.get("name") for c in (fields.get("components") or []) if c.get("name")]
    labels = list(fields.get("labels") or [])
    module = components[0] if components else (fields.get("project") or {}).get("name") or "Board"
    subtasks = _subtask_titles(fields)
    ac = _acceptance_from_description(description)
    status = map_status(fields)
    priority = map_priority((fields.get("priority") or {}).get("name"))
    assignee = fields.get("assignee") or {}
    issue_type = (fields.get("issuetype") or {}).get("name") or "Task"

    # Put subtasks into a single layer so TicketCard progress still works
    frontend_tasks = subtasks[:] if subtasks else []
    if not frontend_tasks and description:
        # Use short bullet lines as lightweight checklist items
        bullets = [
            ln.strip(" -*\t")
            for ln in description.splitlines()
            if ln.strip().startswith(("-", "*", "•"))
        ]
        frontend_tasks = bullets[:12]

    return {
        "id": key,
        "title": fields.get("summary") or key,
        "description": description[:2000] if description else "",
        "acceptance_criteria": ac,
        "priority": priority,
        "story_points": None,
        "labels": labels + ([issue_type] if issue_type else []),
        "tasks": subtasks,
        "frontend_tasks": frontend_tasks,
        "backend_tasks": [],
        "db_tasks": [],
        "module_name": module,
        "source_snippet": None,
        "status": status,
        "jira_meta": {
            "status_name": (fields.get("status") or {}).get("name"),
            "status_id": (fields.get("status") or {}).get("id"),
            "status_category": (
                ((fields.get("status") or {}).get("statusCategory") or {}).get("key")
            ),
            "assignee": assignee.get("displayName") or assignee.get("emailAddress"),
            "issue_type": issue_type,
            "updated": fields.get("updated"),
            "created": fields.get("created"),
            "duedate": fields.get("duedate"),
            "url": None,  # filled by caller with base_url
        },
    }


def issues_to_jira_data(
    issues: list[dict],
    *,
    project_name: str,
    base_url: str,
) -> dict[str, Any]:
    modules: dict[str, list] = {}
    for issue in issues:
        story = issue_to_story(issue)
        if story.get("jira_meta") is not None:
            story["jira_meta"]["url"] = f"{base_url}/browse/{story['id']}"
        mod = story.get("module_name") or "Board"
        modules.setdefault(mod, []).append(story)

    module_list = [{"name": name, "stories": stories} for name, stories in modules.items()]
    total_tasks = sum(
        len(s.get("frontend_tasks") or [])
        + len(s.get("backend_tasks") or [])
        + len(s.get("db_tasks") or [])
        for stories in modules.values()
        for s in stories
    )

    return {
        "project_name": project_name,
        "modules": module_list,
        "common_components": [],
        "stats": {
            "modules": len(module_list),
            "stories": len(issues),
            "tasks": total_tasks,
            "components": 0,
        },
        "source": "jira",
    }


def search_issues(
    cfg: dict[str, str],
    jql: str,
    *,
    max_results: int = 100,
    fields: list[str] | None = None,
) -> list[dict]:
    """Paginated Jira issue search via /rest/api/3/search/jql (legacy /search is gone)."""
    issues: list[dict] = []
    page_size = min(max(1, max_results), 100)
    next_page_token: str | None = None

    while len(issues) < max_results:
        body: dict[str, Any] = {
            "jql": jql,
            "maxResults": min(page_size, max_results - len(issues)),
            "fields": fields or ISSUE_FIELDS,
        }
        if next_page_token:
            body["nextPageToken"] = next_page_token

        data = _request(cfg, "POST", "/rest/api/3/search/jql", json_body=body)
        batch = (data or {}).get("issues") or []
        issues.extend(batch)
        next_page_token = (data or {}).get("nextPageToken")
        if not batch or not next_page_token or len(issues) >= max_results:
            break

    return issues[:max_results]


def get_issue_briefs(cfg: dict[str, str], issue_keys: list[str]) -> dict[str, dict[str, Any]]:
    """Return key -> {summary, project_key, project_name, url} for given issue keys."""
    keys = [k.strip() for k in issue_keys if k and k.strip()]
    if not keys:
        return {}

    out: dict[str, dict[str, Any]] = {}
    base_url = (cfg.get("base_url") or "").rstrip("/")
    chunk_size = 50
    for i in range(0, len(keys), chunk_size):
        chunk = keys[i : i + chunk_size]
        quoted = ",".join(f'"{k}"' for k in chunk)
        jql = f"key in ({quoted}) ORDER BY updated DESC"
        issues = search_issues(
            cfg, jql, max_results=len(chunk), fields=ISSUE_BRIEF_FIELDS
        )
        for issue in issues:
            key = issue.get("key")
            if not key:
                continue
            fields = issue.get("fields") or {}
            project = fields.get("project") or {}
            out[key] = {
                "summary": fields.get("summary") or key,
                "project_key": project.get("key") or "",
                "project_name": project.get("name") or "",
                "url": f"{base_url}/browse/{key}" if base_url else None,
            }
    return out


def list_my_projects(cfg: dict[str, str], max_results: int = 50) -> list[dict]:
    """
    Projects the logged-in user is connected to (assignee, reporter, or watcher),
    not every browseable project on the site.
    """
    jql = (
        "assignee = currentUser() OR reporter = currentUser() OR watcher = currentUser() "
        "ORDER BY updated DESC"
    )
    # Pull a wider issue sample so we discover more projects
    issues = search_issues(cfg, jql, max_results=max(max_results * 4, 100))
    projects: list[dict] = []
    seen: set[str] = set()
    for issue in issues:
        project = ((issue.get("fields") or {}).get("project")) or {}
        key = project.get("key")
        if not key or key in seen:
            continue
        seen.add(key)
        projects.append(
            {
                "id": project.get("id") or key,
                "key": key,
                "name": project.get("name") or key,
                "project_type": project.get("projectTypeKey"),
                "avatar": ((project.get("avatarUrls") or {}).get("48x48")),
            }
        )
        if len(projects) >= max_results:
            break
    projects.sort(key=lambda p: (p.get("name") or "").lower())
    return projects


def resolve_current_user(cfg: dict[str, str]) -> dict[str, Any]:
    """
    Best-effort identity for the logged-in user.
    Tries /myself, then infers from issues assigned to currentUser().
    """
    try:
        me = _request(cfg, "GET", "/rest/api/3/myself")
        return {
            "account_id": me.get("accountId"),
            "display_name": me.get("displayName"),
            "email": me.get("emailAddress"),
        }
    except JiraApiError:
        pass

    try:
        issues = search_issues(
            cfg,
            "assignee = currentUser() ORDER BY updated DESC",
            max_results=5,
        )
        for issue in issues:
            assignee = ((issue.get("fields") or {}).get("assignee")) or {}
            if assignee.get("displayName") or assignee.get("emailAddress"):
                return {
                    "account_id": assignee.get("accountId"),
                    "display_name": assignee.get("displayName"),
                    "email": assignee.get("emailAddress"),
                }
        # Fall back to reporter on issues the user reported
        issues = search_issues(
            cfg,
            "reporter = currentUser() ORDER BY updated DESC",
            max_results=5,
        )
        for issue in issues:
            reporter = ((issue.get("fields") or {}).get("reporter")) or {}
            if reporter.get("displayName") or reporter.get("emailAddress"):
                return {
                    "account_id": reporter.get("accountId"),
                    "display_name": reporter.get("displayName"),
                    "email": reporter.get("emailAddress"),
                }
    except JiraApiError:
        pass

    return {
        "account_id": None,
        "display_name": None,
        "email": None,
    }


def fetch_board_issues(cfg: dict[str, str], board_id: str, max_results: int = 100) -> list[dict]:
    """
    Prefer Agile board API when scopes allow it; otherwise fall back to JQL by board.
    """
    try:
        issues: list[dict] = []
        start_at = 0
        page_size = 50

        while len(issues) < max_results:
            data = _request(
                cfg,
                "GET",
                f"/rest/agile/1.0/board/{board_id}/issue",
                params={
                    "startAt": start_at,
                    "maxResults": min(page_size, max_results - len(issues)),
                    "fields": ",".join(ISSUE_FIELDS),
                },
            )
            batch = data.get("issues") or []
            issues.extend(batch)
            total = data.get("total", 0)
            start_at += len(batch)
            if not batch or start_at >= total or len(issues) >= max_results:
                break

        return issues[:max_results]
    except JiraApiError as exc:
        if exc.status_code not in (401, 403):
            raise
        # Classic read:jira-work often cannot call Agile APIs; use JQL fallback.
        jql = f"board = {int(board_id)} ORDER BY Rank ASC" if str(board_id).isdigit() else (
            f'project = "{board_id}" ORDER BY updated DESC'
        )
        try:
            return search_issues(cfg, jql, max_results=max_results)
        except JiraApiError:
            raise exc


def list_boards(cfg: dict[str, str], project_key: str | None = None) -> list[dict]:
    params: dict[str, Any] = {"maxResults": 50}
    if project_key:
        params["projectKeyOrId"] = project_key
    try:
        data = _request(cfg, "GET", "/rest/agile/1.0/board", params=params)
    except JiraApiError as exc:
        if exc.status_code in (401, 403):
            # No Jira Software board scope — approximate with user projects.
            return [
                {
                    "id": p["key"],
                    "name": p["name"],
                    "type": "project",
                    "project_key": p["key"],
                }
                for p in list_my_projects(cfg)
            ]
        raise
    boards = []
    for b in data.get("values") or []:
        boards.append(
            {
                "id": b.get("id"),
                "name": b.get("name"),
                "type": b.get("type"),
                "project_key": ((b.get("location") or {}).get("projectKey")),
            }
        )
    return boards


def get_project_workflow_statuses(
    cfg: dict[str, str], project_key: str
) -> list[dict[str, Any]]:
    """
    Ordered Jira workflow statuses for a project — from board column config when
    available, otherwise from project status metadata.
    """
    key = re.sub(r"[^A-Za-z0-9_\-]", "", project_key)
    seen: set[str] = set()
    statuses: list[dict[str, Any]] = []

    def add_status(
        st: dict[str, Any], *, column_name: str | None = None, order: int
    ) -> None:
        name = (st.get("name") or "").strip()
        if not name or name in seen:
            return
        seen.add(name)
        cat = st.get("statusCategory") or {}
        statuses.append(
            {
                "id": str(st.get("id") or name),
                "name": name,
                "category": (cat.get("key") or "").lower(),
                "category_name": cat.get("name"),
                "column_name": column_name,
                "order": order,
            }
        )

    boards = list_boards(cfg, key)
    board_id = None
    for b in boards:
        if (b.get("type") or "").lower() in ("scrum", "kanban", "simple"):
            board_id = b.get("id")
            break
    if board_id is None and boards:
        board_id = boards[0].get("id")

    if board_id is not None and str(board_id).isdigit():
        try:
            conf = _request(
                cfg, "GET", f"/rest/agile/1.0/board/{board_id}/configuration"
            )
            order = 0
            for col in (conf.get("columnConfig") or {}).get("columns") or []:
                col_name = col.get("name")
                for st in col.get("statuses") or []:
                    add_status(st, column_name=col_name, order=order)
                    order += 1
            if statuses:
                return statuses
        except JiraApiError:
            pass

    try:
        data = _request(cfg, "GET", f"/rest/api/3/project/{key}/statuses")
        order = 0
        for block in data or []:
            for st in block.get("statuses") or []:
                add_status(st, order=order)
                order += 1
    except JiraApiError:
        pass

    return statuses


def health_check(cfg: dict[str, str]) -> dict[str, Any]:
    me = _request(cfg, "GET", "/rest/api/3/myself")
    return {
        "ok": True,
        "account_id": me.get("accountId"),
        "display_name": me.get("displayName"),
        "email": me.get("emailAddress"),
        "base_url": cfg.get("base_url") or None,
        "project_key": cfg.get("project_key") or None,
        "board_id": cfg.get("board_id") or None,
        "auth_type": "oauth" if has_oauth(cfg) else "basic",
        "configured_from_env": has_basic(_env_config()),
    }


def list_projects(cfg: dict[str, str], max_results: int = 50) -> list[dict]:
    """All browseable projects on the site (may be large). Prefer list_my_projects for UX."""
    data = _request(
        cfg,
        "GET",
        "/rest/api/3/project/search",
        params={"maxResults": max_results, "orderBy": "name"},
    )
    values = data.get("values") if isinstance(data, dict) else data
    projects = []
    for p in values or []:
        projects.append(
            {
                "id": p.get("id"),
                "key": p.get("key"),
                "name": p.get("name"),
                "project_type": p.get("projectTypeKey"),
                "avatar": ((p.get("avatarUrls") or {}).get("48x48")),
            }
        )
    return projects


def add_worklog(
    cfg: dict[str, str],
    issue_key: str,
    *,
    seconds: int,
    started_iso: str,
    comment: str | None = None,
) -> dict[str, Any]:
    """Log time on a Jira issue. `started_iso` should be timezone-aware ISO."""
    if seconds < 60:
        # Jira rejects very short worklogs; round up to 1 minute
        seconds = 60

    # Jira expects: 2024-01-01T10:00:00.000+0000
    started = _to_jira_datetime(started_iso)
    body: dict[str, Any] = {
        "timeSpentSeconds": int(seconds),
        "started": started,
    }
    if comment:
        body["comment"] = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": comment}],
                }
            ],
        }

    return _request(
        cfg,
        "POST",
        f"/rest/api/3/issue/{issue_key}/worklog",
        json_body=body,
    )


def get_transitions(cfg: dict[str, str], issue_key: str) -> list[dict[str, Any]]:
    data = _request(cfg, "GET", f"/rest/api/3/issue/{issue_key}/transitions")
    out: list[dict[str, Any]] = []
    for t in data.get("transitions") or []:
        to = t.get("to") or {}
        out.append(
            {
                "id": str(t.get("id")),
                "name": t.get("name"),
                "to_status": to.get("name"),
                "to_status_id": to.get("id"),
                "to_status_category": ((to.get("statusCategory") or {}).get("key")),
            }
        )
    return out


# Preferred Jira status names for each workflow column (first match wins).
STATUS_COLUMN_TARGETS: dict[str, list[str]] = {
    "To Do": ["To Do", "Open", "Backlog"],
    "Working now": ["In Progress", "QA In Progress"],
    "Dev done": ["Dev Completed", "Ready for QA"],
    "Done after QA verified": ["Done"],
}


def transition_issue(
    cfg: dict[str, str],
    issue_key: str,
    *,
    target_status: str | None = None,
    transition_id: str | None = None,
) -> dict[str, Any]:
    """
    Move an issue to a target Jira status via available transitions.
    `target_status` can be a real Jira status name or a board column label
    like "To Do" / "Working now" / "Dev done" / "Done after QA verified".
    """
    transitions = get_transitions(cfg, issue_key)
    chosen = None

    if transition_id:
        chosen = next((t for t in transitions if t["id"] == str(transition_id)), None)
        if not chosen:
            raise JiraConfigError(
                f"Transition id {transition_id} is not available for {issue_key}."
            )
    elif target_status:
        wanted = STATUS_COLUMN_TARGETS.get(target_status, [target_status])
        wanted_l = [w.lower() for w in wanted]
        # Prefer exact "to_status" match, then transition name match
        for w in wanted_l:
            chosen = next(
                (t for t in transitions if (t.get("to_status") or "").lower() == w),
                None,
            )
            if chosen:
                break
        if not chosen:
            for w in wanted_l:
                chosen = next(
                    (t for t in transitions if (t.get("name") or "").lower() == w),
                    None,
                )
                if chosen:
                    break
        if not chosen:
            available = ", ".join(
                f"{t['name']}→{t['to_status']}" for t in transitions
            ) or "none"
            raise JiraConfigError(
                f"No transition to '{target_status}' for {issue_key}. "
                f"Available: {available}"
            )
    else:
        raise JiraConfigError("Provide target_status or transition_id")

    _request(
        cfg,
        "POST",
        f"/rest/api/3/issue/{issue_key}/transitions",
        json_body={"transition": {"id": chosen["id"]}},
    )
    return {
        "ok": True,
        "issue_key": issue_key,
        "transition_id": chosen["id"],
        "transition_name": chosen.get("name"),
        "status_name": chosen.get("to_status"),
    }


def _to_jira_datetime(iso_str: str) -> str:
    """Convert ISO datetime to Jira worklog format."""
    from datetime import datetime

    raw = iso_str.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return iso_str
    # Jira wants +0000 not +00:00
    formatted = dt.strftime("%Y-%m-%dT%H:%M:%S.000%z")
    if len(formatted) >= 5 and formatted[-5] in "+-" and formatted[-3] != ":":
        return formatted
    # fromisoformat may not include tz; assume UTC
    if dt.tzinfo is None:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
    offset = dt.strftime("%z")  # +0000
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000") + offset


def build_project_jql(project_key: str, *, current_sprint: bool = True) -> str:
    key = re.sub(r"[^A-Za-z0-9_\-]", "", project_key)
    parts = [f'project = "{key}"']
    if current_sprint:
        # Active/open sprint(s) on the project's board(s)
        parts.append("sprint in openSprints()")
    return " AND ".join(parts) + " ORDER BY Rank ASC, priority DESC, updated DESC"


def build_current_sprint_jql(project_key: str) -> str:
    return build_project_jql(project_key, current_sprint=True)


def build_today_jql(project_key: str | None = None) -> str:
    parts = [
        "assignee = currentUser()",
        "updated >= startOfDay()",
    ]
    if project_key:
        key = re.sub(r"[^A-Za-z0-9_\-]", "", project_key)
        parts.insert(0, f'project = "{key}"')
    return " AND ".join(parts) + " ORDER BY priority DESC, updated DESC"


def build_my_open_jql(project_key: str | None = None) -> str:
    parts = [
        "assignee = currentUser()",
        "statusCategory != Done",
    ]
    if project_key:
        key = re.sub(r"[^A-Za-z0-9_\-]", "", project_key)
        parts.insert(0, f'project = "{key}"')
    return " AND ".join(parts) + " ORDER BY priority DESC, updated DESC"
