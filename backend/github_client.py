"""GitHub commits client for the GitHub Daily tab (isolated from Jira worklogs)."""

from __future__ import annotations

import os
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import http_client

JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


class GitHubConfigError(Exception):
    pass


class GitHubApiError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _cfg_from_env() -> dict[str, str]:
    return {
        "token": (os.getenv("GITHUB_TOKEN") or "").strip(),
        "username": (os.getenv("GITHUB_USERNAME") or "").strip(),
        "repos": (os.getenv("GITHUB_REPOS") or "").strip(),
        "author_email": (os.getenv("GITHUB_AUTHOR_EMAIL") or "").strip().lower(),
    }


def resolve_credentials(
    *,
    token: str | None = None,
    username: str | None = None,
    repos_csv: str | None = None,
    author_email: str | None = None,
) -> dict[str, str]:
    """Per-request credentials win; .env is only an optional fallback."""
    env = _cfg_from_env()
    tok = (token or "").strip() or env["token"]
    user = (username or "").strip() or env["username"]
    repos = (repos_csv if repos_csv is not None else env["repos"]) or ""
    email = (author_email or "").strip().lower() or env["author_email"]
    if not tok:
        raise GitHubConfigError(
            "GitHub token is required. Connect with your PAT on the GitHub Daily tab "
            "(or set GITHUB_TOKEN in backend/.env)."
        )
    if not user:
        raise GitHubConfigError(
            "GitHub username is required. Enter your GitHub login on the GitHub Daily tab."
        )
    return {
        "token": tok,
        "username": user,
        "repos": repos.strip(),
        "author_email": email,
    }


def verify_token(token: str) -> dict[str, Any]:
    """Validate PAT and return the authenticated GitHub user."""
    tok = (token or "").strip()
    if not tok:
        raise GitHubConfigError("GitHub token is required.")
    data = _get_json("https://api.github.com/user", tok)
    login = (data.get("login") or "").strip() if isinstance(data, dict) else ""
    if not login:
        raise GitHubApiError("Could not read GitHub user from token.", 502)
    return {
        "login": login,
        "name": (data.get("name") or "").strip() if isinstance(data, dict) else "",
        "email": (data.get("email") or "").strip() if isinstance(data, dict) else "",
        "avatar_url": data.get("avatar_url") if isinstance(data, dict) else None,
        "html_url": data.get("html_url") if isinstance(data, dict) else None,
    }


def _repo_brief(repo: dict[str, Any]) -> dict[str, Any] | None:
    full = (repo.get("full_name") or "").strip()
    if not full:
        return None
    return {
        "full_name": full,
        "name": repo.get("name") or full.split("/")[-1],
        "owner": ((repo.get("owner") or {}).get("login") or ""),
        "private": bool(repo.get("private")),
        "html_url": repo.get("html_url") or f"https://github.com/{full}",
        "description": (repo.get("description") or "")[:160],
        "updated_at": repo.get("updated_at") or "",
        "default_branch": repo.get("default_branch") or "main",
    }


def _paginate_repos(
    token: str,
    url: str,
    params: dict[str, Any],
    *,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page = 1
    while page <= max_pages:
        data = _get_json(url, token, {**params, "page": page, "per_page": 100})
        if not isinstance(data, list) or not data:
            break
        for repo in data:
            brief = _repo_brief(repo)
            if brief:
                out.append(brief)
        if len(data) < 100:
            break
        page += 1
    return out


def list_user_orgs(token: str) -> list[dict[str, Any]]:
    """Orgs the user belongs to (requires read:org + org approving the OAuth App)."""
    tok = (token or "").strip()
    if not tok:
        return []
    orgs: list[dict[str, Any]] = []
    page = 1
    while page <= 10:
        data = _get_json(
            "https://api.github.com/user/orgs",
            tok,
            {"per_page": 100, "page": page},
        )
        if not isinstance(data, list) or not data:
            break
        for org in data:
            login = (org.get("login") or "").strip()
            if login:
                orgs.append(
                    {
                        "login": login,
                        "description": (org.get("description") or "")[:120],
                    }
                )
        if len(data) < 100:
            break
        page += 1
    return orgs


def list_accessible_repos(token: str, *, max_pages: int = 20) -> list[dict[str, Any]]:
    """
    Projects assigned to / worked on by this user only:
    repos they own, are a collaborator on, or have write/maintain/admin on
    (excludes org repos they can only read).
    """
    tok = (token or "").strip()
    if not tok:
        raise GitHubConfigError("GitHub token is required.")

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    page = 1
    while page <= max_pages:
        data = _get_json(
            "https://api.github.com/user/repos",
            tok,
            {
                "affiliation": "owner,collaborator,organization_member",
                "visibility": "all",
                "sort": "updated",
                "direction": "desc",
                "per_page": 100,
                "page": page,
            },
        )
        if not isinstance(data, list) or not data:
            break
        for repo in data:
            perms = repo.get("permissions") or {}
            # Assigned / can work on it — not every readable org repo.
            if perms and not (
                perms.get("admin") or perms.get("maintain") or perms.get("push")
            ):
                continue
            brief = _repo_brief(repo)
            if not brief:
                continue
            key = brief["full_name"]
            if key in seen:
                continue
            seen.add(key)
            out.append(brief)
        if len(data) < 100:
            break
        page += 1

    out.sort(key=lambda r: (r.get("updated_at") or ""), reverse=True)
    return out

def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ai-jira-generator-github-daily",
    }


def _day_bounds_utc(target_day: date, tz: ZoneInfo) -> tuple[str, str]:
    start_local = datetime.combine(target_day, time.min, tzinfo=tz)
    end_local = datetime.combine(target_day + timedelta(days=1), time.min, tzinfo=tz)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)

    def fmt(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    return fmt(start_utc), fmt(end_utc)


def _parse_repos(repos_csv: str) -> list[str]:
    return [x.strip() for x in (repos_csv or "").split(",") if x.strip()]


def _normalize_commit(raw: dict[str, Any], repo_full: str | None = None) -> dict[str, Any] | None:
    sha = (raw.get("sha") or "")[:40]
    commit = raw.get("commit") or {}
    author = commit.get("author") or {}
    message = (commit.get("message") or "").strip()
    first_line = message.split("\n", 1)[0].strip() if message else "(no message)"
    authored = author.get("date") or ""
    html_url = raw.get("html_url") or ""
    if not html_url and repo_full and sha:
        html_url = f"https://github.com/{repo_full}/commit/{sha}"

    full_name = repo_full
    if not full_name:
        repo = raw.get("repository") or {}
        full_name = repo.get("full_name") or ""
    if not full_name and html_url:
        # https://github.com/owner/repo/commit/sha
        parts = html_url.split("github.com/", 1)
        if len(parts) == 2:
            segs = parts[1].split("/")
            if len(segs) >= 2:
                full_name = f"{segs[0]}/{segs[1]}"

    project = (full_name or "").split("/")[-1] if full_name else ""
    jira_keys = sorted(set(JIRA_KEY_RE.findall(message)))
    return {
        "sha": sha,
        "short_sha": sha[:7],
        "message": first_line,
        "full_message": message,
        "authored_at": authored,
        "author_name": author.get("name") or "",
        "author_email": (author.get("email") or "").lower(),
        "html_url": html_url,
        "repo": full_name or "",
        "project": project,
        "jira_keys": jira_keys,
    }


def _get_json(url: str, token: str, params: dict[str, Any] | None = None) -> Any:
    resp = http_client.external_request(
        "GET",
        url,
        headers=_headers(token),
        params=params or {},
        timeout=45,
    )
    if resp.status_code == 401:
        raise GitHubApiError("GitHub auth failed — check GITHUB_TOKEN.", 401)
    if resp.status_code == 403:
        raise GitHubApiError(
            f"GitHub access denied: {(resp.text or '')[:240]}", 403
        )
    if resp.status_code == 404:
        raise GitHubApiError(f"GitHub resource not found: {url}", 404)
    if resp.status_code >= 400:
        raise GitHubApiError(
            f"GitHub API error {resp.status_code}: {(resp.text or '')[:300]}",
            resp.status_code,
        )
    return resp.json()


def _commits_for_repo(
    token: str,
    repo: str,
    username: str,
    since: str,
    until: str,
    author_email: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page = 1
    while page <= 10:
        data = _get_json(
            f"https://api.github.com/repos/{repo}/commits",
            token,
            {
                "author": username,
                "since": since,
                "until": until,
                "per_page": 100,
                "page": page,
            },
        )
        if not isinstance(data, list) or not data:
            break
        for item in data:
            norm = _normalize_commit(item, repo_full=repo)
            if not norm:
                continue
            if author_email and norm.get("author_email") and norm["author_email"] != author_email:
                continue
            out.append(norm)
        if len(data) < 100:
            break
        page += 1
    return out


def _commits_via_search(
    token: str,
    username: str,
    target_day: date,
    author_email: str,
) -> list[dict[str, Any]]:
    # author-date is calendar day in commit author timezone; good enough when repos unset.
    q = f"author:{username} author-date:{target_day.isoformat()}"
    data = _get_json(
        "https://api.github.com/search/commits",
        token,
        {"q": q, "sort": "author-date", "order": "asc", "per_page": 100},
    )
    items = data.get("items") if isinstance(data, dict) else None
    out: list[dict[str, Any]] = []
    for item in items or []:
        norm = _normalize_commit(item)
        if not norm:
            continue
        if author_email and norm.get("author_email") and norm["author_email"] != author_email:
            continue
        out.append(norm)
    return out


def list_commits_for_day(
    target_day: date,
    tz: ZoneInfo,
    *,
    token: str | None = None,
    username: str | None = None,
    repos_csv: str | None = None,
    author_email: str | None = None,
) -> list[dict[str, Any]]:
    cfg = resolve_credentials(
        token=token,
        username=username,
        repos_csv=repos_csv,
        author_email=author_email,
    )
    tok = cfg["token"]
    user = cfg["username"]
    repos = _parse_repos(cfg["repos"])
    email_filter = cfg["author_email"]
    if not repos:
        raise GitHubConfigError(
            "Select at least one connected GitHub project (repo) before loading commits."
        )
    since, until = _day_bounds_utc(target_day, tz)

    commits: list[dict[str, Any]] = []
    for repo in repos:
        commits.extend(
            _commits_for_repo(tok, repo, user, since, until, email_filter)
        )

    # De-dupe by sha, sort by authored time
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for c in commits:
        sha = c.get("sha") or ""
        if not sha or sha in seen:
            continue
        seen.add(sha)
        unique.append(c)

    def sort_key(c: dict[str, Any]) -> str:
        return c.get("authored_at") or ""

    unique.sort(key=sort_key)
    return unique


def github_status(
    *,
    token: str | None = None,
    username: str | None = None,
    repos_csv: str | None = None,
) -> dict[str, Any]:
    """Non-secret config check for the UI (request overrides or .env fallback)."""
    env = _cfg_from_env()
    tok = (token or "").strip() or env["token"]
    user = (username or "").strip() or env["username"]
    repos = _parse_repos(
        repos_csv if repos_csv is not None else env["repos"]
    )
    return {
        "configured": bool(tok) and bool(user),
        "token_set": bool(tok),
        "username": user,
        "repos": repos,
        "author_email": env["author_email"],
        "source": "request" if (token or username) else ("env" if tok else "none"),
    }
