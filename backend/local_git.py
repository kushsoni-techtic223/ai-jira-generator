"""Local git helpers for GitHub Daily local-branch timer (isolated from Jira)."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from typing import Any


class LocalGitError(Exception):
    pass


def _run_git(
    repo_path: str,
    args: list[str],
    *,
    timeout: int = 30,
    env: dict[str, str] | None = None,
) -> str:
    path = os.path.abspath(os.path.expanduser((repo_path or "").strip()))
    if not path or not os.path.isdir(path):
        raise LocalGitError(f"Repo path does not exist: {repo_path}")
    git_dir = os.path.join(path, ".git")
    if not os.path.isdir(git_dir) and not os.path.isfile(git_dir):
        raise LocalGitError(f"Not a git repository: {path}")
    run_env = None
    if env:
        run_env = {**os.environ, **env}
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=run_env,
        )
    except FileNotFoundError as exc:
        raise LocalGitError("git is not installed or not on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise LocalGitError("git command timed out.") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise LocalGitError(err[:400])
    # git push often prints progress / "Everything up-to-date" on stderr
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if out and err:
        return f"{out}\n{err}"
    return out or err


def _github_full_name_from_remote(remote: str) -> str:
    if not remote or "github.com" not in remote:
        return ""
    cleaned = remote.replace(".git", "")
    if cleaned.startswith("git@"):
        cleaned = cleaned.split(":", 1)[-1]
    elif "github.com/" in cleaned:
        cleaned = cleaned.split("github.com/", 1)[-1]
    return cleaned.strip("/")


def inspect_repo(repo_path: str) -> dict[str, Any]:
    path = os.path.abspath(os.path.expanduser((repo_path or "").strip()))
    branch = _run_git(path, ["rev-parse", "--abbrev-ref", "HEAD"])
    remote = ""
    try:
        remote = _run_git(path, ["remote", "get-url", "origin"])
    except LocalGitError:
        pass
    project = os.path.basename(path.rstrip(os.sep))
    full_name = _github_full_name_from_remote(remote)
    return {
        "repo_path": path,
        "branch": branch,
        "remote": remote,
        "project": project,
        "full_name": full_name or project,
    }


def status_summary(repo_path: str) -> dict[str, Any]:
    path = os.path.abspath(os.path.expanduser((repo_path or "").strip()))
    porcelain = _run_git(path, ["status", "--porcelain"])
    lines = [ln for ln in porcelain.splitlines() if ln.strip()]
    staged = sum(1 for ln in lines if ln[0] not in (" ", "?"))
    unstaged = sum(1 for ln in lines if len(ln) > 1 and ln[1] not in (" ",))
    untracked = sum(1 for ln in lines if ln.startswith("??"))
    return {
        "dirty": bool(lines),
        "changed_files": len(lines),
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "files": [ln[3:] if len(ln) >= 4 else ln for ln in lines[:30]],
    }


def _local_branch_exists(repo_path: str, branch: str) -> bool:
    try:
        _run_git(repo_path, ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"])
        return True
    except LocalGitError:
        return False


def _remote_branch_exists(repo_path: str, remote: str, branch: str) -> bool:
    try:
        _run_git(
            repo_path,
            ["show-ref", "--verify", "--quiet", f"refs/remotes/{remote}/{branch}"],
        )
        return True
    except LocalGitError:
        return False


def ensure_branch(
    repo_path: str,
    branch: str,
    *,
    remote: str = "origin",
) -> dict[str, Any]:
    """
    Switch to `branch`, creating it locally if needed.
    Order: already on it → local checkout → track remote → create new from HEAD.
    New branches are published later by push_to_remote (-u).
    """
    path = os.path.abspath(os.path.expanduser((repo_path or "").strip()))
    target = (branch or "").strip()
    if not target or target == "HEAD":
        raise LocalGitError("A real branch name is required.")

    current = _run_git(path, ["rev-parse", "--abbrev-ref", "HEAD"])
    if current == target:
        return {
            "branch": target,
            "created": False,
            "from_remote": False,
            "repo_path": path,
        }

    remote_name = (remote or "origin").strip() or "origin"
    created = False
    from_remote = False

    if _local_branch_exists(path, target):
        _run_git(path, ["checkout", target])
    else:
        # Best-effort: learn remote branches (ignore network failures).
        try:
            _run_git(path, ["fetch", remote_name, target, "--depth", "1"], timeout=60)
        except LocalGitError:
            try:
                _run_git(path, ["fetch", remote_name, "--prune"], timeout=90)
            except LocalGitError:
                pass

        if _remote_branch_exists(path, remote_name, target):
            _run_git(
                path,
                ["checkout", "-b", target, "--track", f"{remote_name}/{target}"],
            )
            from_remote = True
        else:
            # Branch missing locally and on remote → create from current HEAD.
            _run_git(path, ["checkout", "-b", target])
            created = True

    return {
        "branch": target,
        "created": created,
        "from_remote": from_remote,
        "repo_path": path,
    }


def create_commit(
    repo_path: str,
    *,
    message: str,
    add_all: bool = True,
    branch: str | None = None,
) -> dict[str, Any]:
    """
    Create a local commit from the app (GitHub Daily timer only).
    Ensures target branch exists (creates it if missing), then stages/commits.
    """
    path = os.path.abspath(os.path.expanduser((repo_path or "").strip()))
    msg = (message or "").strip()
    if not msg:
        raise LocalGitError("Commit message is required.")

    info = inspect_repo(path)
    target_branch = (branch or "").strip()
    branch_meta: dict[str, Any] = {"created": False, "from_remote": False}
    if target_branch and target_branch != info["branch"]:
        branch_meta = ensure_branch(path, target_branch)
    elif target_branch and not _local_branch_exists(path, target_branch):
        # Named branch but somehow not present — create/switch.
        branch_meta = ensure_branch(path, target_branch)

    if add_all:
        _run_git(path, ["add", "-A"])

    # Nothing to commit?
    status = _run_git(path, ["status", "--porcelain"])
    if not status.strip():
        raise LocalGitError("Nothing to commit — working tree is clean.")

    # Allow commit even if hooks fail? keep strict.
    _run_git(path, ["commit", "-m", msg], timeout=60)
    sha = _run_git(path, ["rev-parse", "HEAD"])
    short = sha[:7]
    branch_now = _run_git(path, ["rev-parse", "--abbrev-ref", "HEAD"])
    return {
        "ok": True,
        "sha": sha,
        "short_sha": short,
        "message": msg,
        "branch": branch_now,
        "repo_path": path,
        "branch_created": bool(branch_meta.get("created")),
    }


def push_to_remote(
    repo_path: str,
    *,
    branch: str | None = None,
    remote: str = "origin",
    token: str | None = None,
) -> dict[str, Any]:
    """
    Push current branch to GitHub (or configured remote).
    Creates the remote branch on first push (-u) if it does not exist yet.
    Prefer OAuth/PAT token for github.com HTTPS auth; otherwise uses local git credentials/SSH.
    """
    path = os.path.abspath(os.path.expanduser((repo_path or "").strip()))
    info = inspect_repo(path)
    target_branch = (branch or "").strip() or info["branch"]
    if target_branch == "HEAD":
        raise LocalGitError("Detached HEAD — checkout a branch before pushing.")

    # Ensure local branch exists / is checked out before publishing to GitHub.
    branch_meta = ensure_branch(path, target_branch, remote=remote)

    remote_name = (remote or "origin").strip() or "origin"
    remote_url = ""
    try:
        remote_url = _run_git(path, ["remote", "get-url", remote_name])
    except LocalGitError as exc:
        raise LocalGitError(
            f"No git remote '{remote_name}'. Add a GitHub remote first."
        ) from exc

    full_name = _github_full_name_from_remote(remote_url)
    tok = (token or "").strip()

    # Avoid interactive credential prompts from the backend process.
    quiet_env = {
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",
    }

    if tok and full_name:
        # One-shot HTTPS URL with token — does not rewrite stored remote.
        push_url = f"https://x-access-token:{tok}@github.com/{full_name}.git"
        out = _run_git(
            path,
            ["push", "-u", push_url, f"HEAD:refs/heads/{target_branch}"],
            timeout=120,
            env=quiet_env,
        )
        # Point upstream at the named remote for normal CLI use next time.
        try:
            _run_git(
                path,
                ["branch", f"--set-upstream-to={remote_name}/{target_branch}", target_branch],
                timeout=15,
                env=quiet_env,
            )
        except LocalGitError:
            pass
    else:
        try:
            out = _run_git(
                path,
                ["push", "-u", remote_name, target_branch],
                timeout=120,
                env=quiet_env,
            )
        except LocalGitError as exc:
            hint = str(exc)
            if "Authentication" in hint or "could not read Username" in hint or "denied" in hint.lower():
                raise LocalGitError(
                    "Push failed — connect GitHub in this app (OAuth) so we can "
                    "authenticate, or set up local git credentials/SSH for this repo."
                ) from exc
            raise

    status = status_summary(path)
    combined = (out or "").lower()
    up_to_date = "everything up-to-date" in combined or "up to date" in combined
    return {
        "ok": True,
        "branch": target_branch,
        "remote": remote_name,
        "full_name": full_name or info.get("full_name") or "",
        "output": (out or "")[:300],
        "used_token": bool(tok and full_name),
        "up_to_date": up_to_date,
        "branch_created_local": bool(branch_meta.get("created")),
        "branch_created_remote": not up_to_date or bool(branch_meta.get("created")),
        "dirty": bool(status.get("dirty")),
        "changed_files": int(status.get("changed_files") or 0),
    }


def list_commits_since(
    repo_path: str,
    *,
    branch: str,
    since_iso: str,
    until_iso: str | None = None,
    author: str | None = None,
) -> list[dict[str, Any]]:
    """
    Commits on `branch` between since and until (local git log).
    Does not create commits — only reads history.
    """
    path = os.path.abspath(os.path.expanduser((repo_path or "").strip()))
    br = (branch or "").strip() or "HEAD"
    args = [
        "log",
        br,
        f"--since={since_iso}",
        "--pretty=format:%H%x09%h%x09%aI%x09%an%x09%ae%x09%s",
        "--reverse",
    ]
    if until_iso:
        args.insert(3, f"--until={until_iso}")
    if author and author.strip():
        args.append(f"--author={author.strip()}")

    out = _run_git(path, args)
    commits: list[dict[str, Any]] = []
    if not out:
        return commits
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        sha, short, authored_at, name, email, message = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
        commits.append(
            {
                "sha": sha,
                "short_sha": short,
                "authored_at": authored_at,
                "author_name": name,
                "author_email": email,
                "message": message,
            }
        )
    return commits


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
