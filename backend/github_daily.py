"""GitHub-commits daily task sheet — isolated from Jira worklog email path."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import github_client as gh


def _reload_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        env_path = os.path.join(os.path.dirname(__file__), ".env")
        # Keep desktop/process overrides.
        preserved = {
            key: os.environ[key]
            for key in ("DESKTOP_MODE", "BACKEND_DATA_DIR", "FRONTEND_URL")
            if key in os.environ
        }
        load_dotenv(env_path, override=True)
        os.environ.update(preserved)
    except ImportError:
        pass


def _email_recipients(default_csv: str, override: Optional[list[str]]) -> list[str]:
    if override is not None:
        return [x.strip() for x in override if x and x.strip()]
    return [x.strip() for x in (default_csv or "").split(",") if x.strip()]


def _format_hhmm(seconds: int) -> str:
    mins = max(0, int(seconds) + 59) // 60
    h = mins // 60
    m = mins % 60
    return f"{h}:{m:02d}"


def _pack_github_commits_into_workday(
    rows: list[dict[str, Any]],
    *,
    target_day,
    tz: ZoneInfo,
    day_start_hour: int = 9,
    day_end_hour: int = 18,
) -> list[dict[str, Any]]:
    """
    Fit direct GitHub commits into free gaps of the workday.

    - local_timer rows keep real In/Out.
    - github_commit rows first fill 9 AM → local start, then after local → 6 PM.
    """
    commits = [x for x in rows if x.get("source") == "github_commit"]
    timed = [x for x in rows if x.get("source") != "github_commit"]
    if not commits:
        return rows

    day_start = datetime(
        target_day.year,
        target_day.month,
        target_day.day,
        day_start_hour,
        0,
        0,
        tzinfo=tz,
    )
    day_end = datetime(
        target_day.year,
        target_day.month,
        target_day.day,
        day_end_hour,
        0,
        0,
        tzinfo=tz,
    )

    occupied: list[tuple[datetime, datetime]] = []
    for row in sorted(timed, key=lambda x: x["start_dt"]):
        start = max(row["start_dt"], day_start)
        end = min(row["end_dt"], day_end)
        if end > start:
            occupied.append((start, end))

    merged: list[tuple[datetime, datetime]] = []
    for start, end in occupied:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))

    gaps: list[list[datetime]] = []
    cursor = day_start
    for start, end in merged:
        if start > cursor:
            gaps.append([cursor, start])
        cursor = max(cursor, end)
    if cursor < day_end:
        gaps.append([cursor, day_end])

    packed: list[dict[str, Any]] = []
    for row in sorted(commits, key=lambda x: x["start_dt"]):
        remaining = max(60, int(row.get("seconds") or 60))
        while remaining > 0:
            gap_idx = next(
                (
                    i
                    for i, (g_start, g_end) in enumerate(gaps)
                    if (g_end - g_start).total_seconds() >= 60
                ),
                None,
            )
            if gap_idx is None:
                fallback_start = day_end
                if packed:
                    fallback_start = max(fallback_start, packed[-1]["end_dt"])
                for t in timed:
                    fallback_start = max(fallback_start, t["end_dt"])
                packed.append(
                    {
                        **row,
                        "start_dt": fallback_start,
                        "end_dt": fallback_start + timedelta(seconds=remaining),
                        "seconds": remaining,
                    }
                )
                remaining = 0
                break

            g_start, g_end = gaps[gap_idx]
            gap_sec = int((g_end - g_start).total_seconds())
            take = min(remaining, gap_sec)
            take = max(60, (take // 60) * 60)
            if take > gap_sec:
                take = gap_sec
            if take < 60:
                gaps.pop(gap_idx)
                continue

            start_dt = g_start
            end_dt = start_dt + timedelta(seconds=take)
            packed.append(
                {
                    **row,
                    "start_dt": start_dt,
                    "end_dt": end_dt,
                    "seconds": take,
                }
            )
            remaining -= take
            if end_dt >= g_end or (g_end - end_dt).total_seconds() < 60:
                gaps.pop(gap_idx)
            else:
                gaps[gap_idx][0] = end_dt

    return timed + packed


def _finalize_github_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Sort, format display fields, collapse repeated dates, recompute total."""
    ordered = sorted(rows, key=lambda x: x["start_dt"])
    total_seconds = 0
    prev_date = ""
    out: list[dict[str, Any]] = []
    for idx, row in enumerate(ordered, start=1):
        sec = max(60, int(row.get("seconds") or 0))
        total_seconds += sec
        date_str = row["start_dt"].strftime("%d/%m/%y")
        date_display = date_str if date_str != prev_date else ""
        prev_date = date_str
        summary = row.get("task_summary") or row.get("task") or "Commit"
        cleaned = {
            k: v
            for k, v in row.items()
            if k not in {"start_dt", "end_dt"}
        }
        out.append(
            {
                **cleaned,
                "sr": idx,
                "date": date_str,
                "date_display": date_display,
                "in_time": row["start_dt"].strftime("%-I:%M %p"),
                "out_time": row["end_dt"].strftime("%-I:%M %p"),
                "total_time": _format_hhmm(sec),
                "seconds": sec,
                "task": summary,
                "task_summary": summary,
            }
        )
    return out, total_seconds


def _parse_authored(value: str, tz: ZoneInfo) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=tz)


def _jira_browse_url(issue_key: str) -> str | None:
    base = (os.getenv("JIRA_BASE_URL") or "").rstrip("/")
    if not base or not issue_key:
        return None
    return f"{base}/browse/{issue_key}"


def _selected_repo_set(repos_csv: str | None) -> set[str]:
    return {
        r.strip().lower()
        for r in (repos_csv or "").split(",")
        if r.strip()
    }


def _session_matches_repos(session: dict[str, Any], selected: set[str]) -> bool:
    if not selected:
        return True
    full = (session.get("full_name") or "").strip().lower()
    project = (session.get("project") or "").strip().lower()
    path = (session.get("repo_path") or "").rstrip("/").split("/")[-1].lower()
    for repo in selected:
        if full and full == repo:
            return True
        short = repo.split("/")[-1] if "/" in repo else repo
        if project and project == short:
            return True
        if path and path == short:
            return True
    return False


def _commit_url_index(commits: list[dict[str, Any]]) -> dict[str, str]:
    """Map sha / short_sha → html_url from remote commit search."""
    out: dict[str, str] = {}
    for c in commits:
        url = (c.get("html_url") or "").strip()
        if not url:
            continue
        sha = (c.get("sha") or "").strip().lower()
        short = (c.get("short_sha") or "").strip().lower()
        if sha:
            out[sha] = url
            out[sha[:7]] = url
        if short:
            out[short] = url
    return out


def _task_commits_for_session(
    commits: list[dict[str, Any]],
    session: dict[str, Any],
    url_by_sha: dict[str, str] | None = None,
    *,
    limit: int = 8,
) -> list[dict[str, str]]:
    """One entry per commit: summary + github url (newline-friendly for UI/email)."""
    url_by_sha = url_by_sha or {}
    full = (session.get("full_name") or "").strip()
    items: list[dict[str, str]] = []
    for c in commits[:limit]:
        msg = c.get("message") or c.get("short_sha") or "commit"
        short = (c.get("short_sha") or "")[:7]
        sha = (c.get("sha") or short or "").strip()
        sha_l = sha.lower()
        short_l = short.lower()
        url = (
            (c.get("html_url") or "").strip()
            or url_by_sha.get(sha_l)
            or url_by_sha.get(short_l)
            or url_by_sha.get(sha_l[:7])
        )
        if not url and full and sha:
            url = f"https://github.com/{full}/commit/{sha}"
        items.append(
            {
                "summary": f"{short} {msg}".strip(),
                "url": url or "",
                "short_sha": short,
            }
        )
    return items


def _task_cell_html(row: dict[str, Any]) -> str:
    """Render task cell: each commit on its own line with its link."""
    commits = row.get("task_commits") or []
    parts: list[str] = []
    if commits:
        for c in commits:
            summary = c.get("summary") or "commit"
            url = (c.get("url") or "").strip()
            block = summary
            if url:
                block += f"<br><a href='{url}'>{url}</a>"
            parts.append(block)
    else:
        summary = row.get("task_summary") or ""
        url = (row.get("task_url") or "").strip()
        block = summary
        if url:
            block += f"<br><a href='{url}'>{url}</a>"
        parts.append(block)
    body = "<br><br>".join(parts)
    return f"{body}<span> - {row['total_time']}h</span>"


def _task_cell_text(row: dict[str, Any]) -> str:
    commits = row.get("task_commits") or []
    if commits:
        lines: list[str] = []
        for c in commits:
            summary = c.get("summary") or "commit"
            url = (c.get("url") or "").strip()
            lines.append(f"{summary} ({url})" if url else summary)
        return " | ".join(lines)
    task_text = row.get("task_summary") or ""
    if row.get("task_url"):
        task_text += f" ({row['task_url']})"
    return task_text


def _rows_from_local_sessions(
    sessions: list[dict[str, Any]],
    *,
    target_day,
    tz: ZoneInfo,
    selected: set[str],
    url_by_sha: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """
    One sheet row per saved local timer session (real in/out/total).
    Task cell lists commits linked during that timer.
    """
    url_by_sha = url_by_sha or {}
    rows: list[dict[str, Any]] = []
    total_seconds = 0
    for session in sessions:
        if not _session_matches_repos(session, selected):
            continue
        start_dt = _parse_authored(session.get("started_at") or "", tz)
        end_dt = _parse_authored(session.get("ended_at") or "", tz)
        if not start_dt or not end_dt:
            continue
        if start_dt.date() != target_day:
            continue
        commits = [
            c
            for c in (session.get("commits") or [])
            if (c.get("sha") or c.get("short_sha") or c.get("message"))
        ]
        # Omit timer sessions with no linked commits from demo + daily sheet.
        if not commits and int(session.get("commit_count") or 0) <= 0:
            continue
        sec = int(
            session.get("seconds")
            or max(0, int((end_dt - start_dt).total_seconds()))
        )
        total_seconds += sec
        task_commits = _task_commits_for_session(
            commits, session, url_by_sha, limit=8
        )
        task_summary = (
            "\n".join(c["summary"] for c in task_commits) if task_commits else "Commit"
        )
        if len(commits) > 8:
            task_summary += f"\n(+{len(commits) - 8} more)"
        first_url = next((c["url"] for c in task_commits if c.get("url")), None)
        project = (
            session.get("project")
            or (session.get("full_name") or "").split("/")[-1]
            or "GitHub"
        )
        rows.append(
            {
                "sr": len(rows) + 1,
                "date": start_dt.strftime("%d/%m/%y"),
                "date_display": start_dt.strftime("%d/%m/%y"),
                "in_time": start_dt.strftime("%-I:%M %p"),
                "out_time": end_dt.strftime("%-I:%M %p"),
                "total_time": _format_hhmm(sec),
                "project": project,
                "task": task_summary,
                "task_summary": task_summary,
                "task_url": first_url,
                "task_commits": task_commits,
                "branch": session.get("branch"),
                "repo_path": session.get("repo_path"),
                "commit_count": len(commits) or int(session.get("commit_count") or 0),
                "session_id": session.get("id"),
                "source": "local_timer",
                "start_dt": start_dt,
                "end_dt": end_dt,
                "seconds": sec,
            }
        )
    return rows, total_seconds


def build_github_daily_email_payload(
    req_date: str | None = None,
    req_to: list[str] | None = None,
    req_cc: list[str] | None = None,
    fallback_name: str | None = None,
    *,
    token: str | None = None,
    username: str | None = None,
    repos_csv: str | None = None,
    author_email: str | None = None,
    local_sessions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Build the same sheet shape as the Jira daily email.

    Prefer saved local branch-timer sessions (real In/Out/Total) when available
    for the selected projects. Fall back to remote commits (1 min each) only
    when no matching local sessions exist.
    """
    _reload_dotenv()
    tz_name = os.getenv("DAILY_EMAIL_TZ") or "Asia/Kolkata"
    tz = ZoneInfo(tz_name)
    target_day = (
        datetime.strptime(req_date, "%Y-%m-%d").date()
        if req_date
        else datetime.now(tz).date()
    )

    creds = gh.resolve_credentials(
        token=token,
        username=username,
        repos_csv=repos_csv,
        author_email=author_email,
    )
    commits = gh.list_commits_for_day(
        target_day,
        tz,
        token=creds["token"],
        username=creds["username"],
        repos_csv=creds["repos"],
        author_email=creds["author_email"],
    )
    jira_base = (os.getenv("JIRA_BASE_URL") or "").rstrip("/")
    selected = _selected_repo_set(creds["repos"] or repos_csv)
    url_by_sha = _commit_url_index(commits)

    session_rows, session_seconds = _rows_from_local_sessions(
        local_sessions or [],
        target_day=target_day,
        tz=tz,
        selected=selected,
        url_by_sha=url_by_sha,
    )

    rows: list[dict[str, Any]] = []
    total_seconds = 0
    used_local = False

    if session_rows:
        used_local = True
        rows = session_rows
        total_seconds = session_seconds
        # Cover remote commits that were never linked to a local timer.
        covered: set[str] = set()
        for s in local_sessions or []:
            for c in s.get("commits") or []:
                sha = (c.get("sha") or "").lower()
                short = (c.get("short_sha") or "").lower()
                if sha:
                    covered.add(sha)
                    covered.add(sha[:7])
                if short:
                    covered.add(short)
        for c in commits:
            sha = (c.get("sha") or "").lower()
            short = (c.get("short_sha") or "").lower()
            if (sha and sha in covered) or (short and short in covered):
                continue
            authored = _parse_authored(c.get("authored_at") or "", tz)
            if not authored or authored.date() != target_day:
                continue
            # Keep uncovered commits visible as 1-min placeholders for packing.
            sec = 60
            total_seconds += sec
            summary = c.get("message") or c.get("short_sha") or "Commit"
            end_dt = authored + timedelta(seconds=sec)
            rows.append(
                {
                    "sr": len(rows) + 1,
                    "date": authored.strftime("%d/%m/%y"),
                    "date_display": authored.strftime("%d/%m/%y"),
                    "in_time": authored.strftime("%-I:%M %p"),
                    "out_time": end_dt.strftime("%-I:%M %p"),
                    "total_time": _format_hhmm(sec),
                    "project": c.get("project") or c.get("repo") or "GitHub",
                    "task": summary,
                    "task_summary": f"{summary} (no local timer)",
                    "task_url": c.get("html_url") or None,
                    "sha": c.get("short_sha"),
                    "repo": c.get("repo"),
                    "source": "github_commit",
                    "start_dt": authored,
                    "end_dt": end_dt,
                    "seconds": sec,
                }
            )
    else:
        # One row per commit; duration defaults to 1 minute (no local timer).
        per_commit_seconds = 60
        for idx, c in enumerate(commits, start=1):
            authored = _parse_authored(c.get("authored_at") or "", tz)
            if not authored:
                continue
            if authored.date() != target_day:
                continue
            sec = per_commit_seconds
            total_seconds += sec
            summary = c.get("message") or c.get("short_sha") or "Commit"
            url = c.get("html_url") or ""
            jira_keys = c.get("jira_keys") or []
            extra_links = []
            for key in jira_keys:
                browse = _jira_browse_url(key)
                if browse:
                    extra_links.append(browse)
                elif jira_base:
                    extra_links.append(f"{jira_base}/browse/{key}")

            task_summary = summary
            if jira_keys:
                task_summary = f"{summary} [{', '.join(jira_keys)}]"
            end_dt = authored + timedelta(seconds=sec)

            rows.append(
                {
                    "sr": idx,
                    "date": authored.strftime("%d/%m/%y"),
                    "date_display": authored.strftime("%d/%m/%y"),
                    "in_time": authored.strftime("%-I:%M %p"),
                    "out_time": end_dt.strftime("%-I:%M %p"),
                    "total_time": _format_hhmm(sec),
                    "project": c.get("project") or c.get("repo") or "GitHub",
                    "task": task_summary,
                    "task_summary": task_summary,
                    "task_url": url or (extra_links[0] if extra_links else None),
                    "sha": c.get("short_sha"),
                    "repo": c.get("repo"),
                    "source": "github_commit",
                    "start_dt": authored,
                    "end_dt": end_dt,
                    "seconds": sec,
                }
            )

    # Pack direct GitHub commits into 9 AM–6 PM free gaps around local timers.
    day_start_hour = int(os.getenv("DAILY_EMAIL_DAY_START_HOUR") or "9")
    day_end_hour = int(os.getenv("DAILY_EMAIL_DAY_END_HOUR") or "18")
    rows = _pack_github_commits_into_workday(
        rows,
        target_day=target_day,
        tz=tz,
        day_start_hour=day_start_hour,
        day_end_hour=day_end_hour,
    )
    rows, total_seconds = _finalize_github_rows(rows)

    user_name = (
        (fallback_name or "").strip()
        or (os.getenv("DAILY_EMAIL_USER_NAME") or "").strip()
        or creds["username"]
        or "Team Member"
    )
    subject = (
        f"Today Task sheet (GitHub) : {user_name} [{target_day.strftime('%d-%m-%Y')}]"
    )
    to_csv = os.getenv("GITHUB_DAILY_EMAIL_TO") or os.getenv("DAILY_EMAIL_TO") or ""
    cc_csv = os.getenv("GITHUB_DAILY_EMAIL_CC") or os.getenv("DAILY_EMAIL_CC") or ""
    to_list = _email_recipients(to_csv, req_to)
    cc_list = _email_recipients(cc_csv, req_cc)

    source_note = (
        f"Source: Local branch timers + GitHub ({target_day.isoformat()})"
        if used_local
        else f"Source: GitHub commits ({target_day.isoformat()})"
    )

    # Collapse repeated dates and drop index column for cleaner sheets.
    prev_date = ""
    for r in rows:
        d = r.get("date") or ""
        r["date_display"] = d if d != prev_date else ""
        if d:
            prev_date = d

    tr_html = "".join(
        f"""
        <tr>
          <td style="border:1px solid #999;padding:6px;text-align:center;">{r.get('date_display', r.get('date') or '')}</td>
          <td style="border:1px solid #999;padding:6px;text-align:center;">{r['in_time']}</td>
          <td style="border:1px solid #999;padding:6px;text-align:center;">{r['out_time']}</td>
          <td style="border:1px solid #999;padding:6px;text-align:center;">{r['total_time']}</td>
          <td style="border:1px solid #999;padding:6px;text-align:center;">{r['project']}</td>
          <td style="border:1px solid #999;padding:6px;">
            {_task_cell_html(r)}
          </td>
        </tr>
        """
        for r in rows
    )
    html = f"""
    <div style="font-family:Arial,sans-serif;">
      <p><strong>GREETINGS:</strong><br/>Respected TL/PM/HR,</p>
      <p style="color:#555;font-size:12px;">{source_note}</p>
      <table style="border-collapse:collapse;width:100%;font-size:13px;">
        <thead>
          <tr style="background:#f3c623;">
            <th style="border:1px solid #999;padding:6px;">Date</th>
            <th style="border:1px solid #999;padding:6px;">In-Time</th>
            <th style="border:1px solid #999;padding:6px;">Out-Time</th>
            <th style="border:1px solid #999;padding:6px;">Total Time</th>
            <th style="border:1px solid #999;padding:6px;">Project</th>
            <th style="border:1px solid #999;padding:6px;">Task</th>
          </tr>
        </thead>
        <tbody>
          {tr_html if rows else '<tr><td colspan="6" style="border:1px solid #999;padding:8px;text-align:center;">No commits or local timers found for this day.</td></tr>'}
        </tbody>
      </table>
      <p style="margin-top:12px;"><strong>Total:</strong> {_format_hhmm(total_seconds)}h</p>
      <p style="margin-top:16px;">Regards,<br/>{user_name}</p>
    </div>
    """

    table_lines = [
        "Date | In-Time | Out-Time | Total Time | Project | Task",
    ]
    for r in rows:
        table_lines.append(
            f"{r.get('date_display', r.get('date') or '')} | {r['in_time']} | {r['out_time']} | "
            f"{r['total_time']} | {r['project']} | {_task_cell_text(r)}"
        )
    rows_text = "\n".join(table_lines)
    text_body = (
        "GREETINGS:\n"
        "Respected TL/PM/HR,\n\n"
        f"{rows_text}\n\n"
        f"Total: {_format_hhmm(total_seconds)}h\n\n"
        "Regards,\n"
        f"{user_name}"
    )

    status = gh.github_status(
        token=creds["token"],
        username=creds["username"],
        repos_csv=creds["repos"],
    )
    return {
        "source": "github_local_timer" if used_local else "github",
        "date": str(target_day),
        "subject": subject,
        "to": to_list,
        "cc": cc_list,
        "rows": rows,
        "commits": commits,
        "commit_count": sum(int(r.get("commit_count") or (1 if r.get("sha") else 0)) for r in rows),
        "session_count": sum(1 for r in rows if r.get("source") == "local_timer"),
        "total_seconds": total_seconds,
        "total_time": _format_hhmm(total_seconds),
        "html": html,
        "text_body": text_body,
        "rows_text": rows_text,
        "user_name": user_name,
        "tz": tz_name,
        "github": status,
        "used_local_timers": used_local,
        "smtp_ready": bool(
            (os.getenv("SMTP_HOST") or "").strip()
            and (os.getenv("SMTP_USER") or "").strip()
        ),
    }


def build_github_local_timer_email_payload(
    sessions: list[dict[str, Any]],
    *,
    req_date: str | None = None,
    req_to: list[str] | None = None,
    req_cc: list[str] | None = None,
    fallback_name: str | None = None,
) -> dict[str, Any]:
    """
    Sheet from local GitHub-daily timer sessions (time + linked commits).
    Does not use Jira worklogs or remote commit search.
    """
    _reload_dotenv()
    tz_name = os.getenv("DAILY_EMAIL_TZ") or "Asia/Kolkata"
    tz = ZoneInfo(tz_name)
    target_day = (
        datetime.strptime(req_date, "%Y-%m-%d").date()
        if req_date
        else datetime.now(tz).date()
    )

    rows: list[dict[str, Any]] = []
    total_seconds = 0
    for session in sessions:
        try:
            start_dt = _parse_authored(session.get("started_at") or "", tz)
            end_dt = _parse_authored(session.get("ended_at") or "", tz)
        except Exception:
            start_dt = None
            end_dt = None
        if not start_dt or not end_dt:
            continue
        if start_dt.date() != target_day:
            continue
        commits = [
            c
            for c in (session.get("commits") or [])
            if (c.get("sha") or c.get("short_sha") or c.get("message"))
        ]
        # Omit sessions with no commits from local-timer daily sheet.
        if not commits and int(session.get("commit_count") or 0) <= 0:
            continue
        sec = int(session.get("seconds") or max(0, int((end_dt - start_dt).total_seconds())))
        total_seconds += sec
        task_commits = _task_commits_for_session(commits, session, limit=8)
        task_summary = (
            "\n".join(c["summary"] for c in task_commits)
            if task_commits
            else "Local timer · commit"
        )
        if len(commits) > 8:
            task_summary += f"\n(+{len(commits) - 8} more)"
        first_url = next((c["url"] for c in task_commits if c.get("url")), None)
        project = (
            session.get("project")
            or (session.get("full_name") or "").split("/")[-1]
            or "GitHub"
        )
        rows.append(
            {
                "sr": len(rows) + 1,
                "date": start_dt.strftime("%d/%m/%y"),
                "in_time": start_dt.strftime("%-I:%M %p"),
                "out_time": end_dt.strftime("%-I:%M %p"),
                "total_time": _format_hhmm(sec),
                "project": project,
                "task": task_summary,
                "task_summary": task_summary,
                "task_url": first_url,
                "task_commits": task_commits,
                "branch": session.get("branch"),
                "repo_path": session.get("repo_path"),
                "commit_count": len(commits) or int(session.get("commit_count") or 0),
            }
        )

    user_name = (
        (fallback_name or "").strip()
        or (os.getenv("DAILY_EMAIL_USER_NAME") or "").strip()
        or "Team Member"
    )
    subject = (
        f"Today Task sheet (GitHub local) : {user_name} [{target_day.strftime('%d-%m-%Y')}]"
    )
    to_csv = os.getenv("GITHUB_DAILY_EMAIL_TO") or os.getenv("DAILY_EMAIL_TO") or ""
    cc_csv = os.getenv("GITHUB_DAILY_EMAIL_CC") or os.getenv("DAILY_EMAIL_CC") or ""
    to_list = _email_recipients(to_csv, req_to)
    cc_list = _email_recipients(cc_csv, req_cc)

    # Collapse repeated dates and drop index column for cleaner sheets.
    prev_date = ""
    for r in rows:
        d = r.get("date") or ""
        r["date_display"] = d if d != prev_date else ""
        if d:
            prev_date = d

    tr_html = "".join(
        f"""
        <tr>
          <td style="border:1px solid #999;padding:6px;text-align:center;">{r.get('date_display', r.get('date') or '')}</td>
          <td style="border:1px solid #999;padding:6px;text-align:center;">{r['in_time']}</td>
          <td style="border:1px solid #999;padding:6px;text-align:center;">{r['out_time']}</td>
          <td style="border:1px solid #999;padding:6px;text-align:center;">{r['total_time']}</td>
          <td style="border:1px solid #999;padding:6px;text-align:center;">{r['project']}</td>
          <td style="border:1px solid #999;padding:6px;">
            {_task_cell_html(r)}
          </td>
        </tr>
        """
        for r in rows
    )
    html = f"""
    <div style="font-family:Arial,sans-serif;">
      <p><strong>GREETINGS:</strong><br/>Respected TL/PM/HR,</p>
      <p style="color:#555;font-size:12px;">Source: Local GitHub branch timers with commits ({target_day.isoformat()})</p>
      <table style="border-collapse:collapse;width:100%;font-size:13px;">
        <thead>
          <tr style="background:#f3c623;">
            <th style="border:1px solid #999;padding:6px;">Date</th>
            <th style="border:1px solid #999;padding:6px;">In-Time</th>
            <th style="border:1px solid #999;padding:6px;">Out-Time</th>
            <th style="border:1px solid #999;padding:6px;">Total Time</th>
            <th style="border:1px solid #999;padding:6px;">Project</th>
            <th style="border:1px solid #999;padding:6px;">Task</th>
          </tr>
        </thead>
        <tbody>
          {tr_html if rows else '<tr><td colspan="6" style="border:1px solid #999;padding:8px;text-align:center;">No local timer sessions with commits for this day.</td></tr>'}
        </tbody>
      </table>
      <p style="margin-top:12px;"><strong>Total:</strong> {_format_hhmm(total_seconds)}h</p>
      <p style="margin-top:16px;">Regards,<br/>{user_name}</p>
    </div>
    """

    table_lines = [
        "Date | In-Time | Out-Time | Total Time | Project | Task",
    ]
    for r in rows:
        table_lines.append(
            f"{r.get('date_display', r.get('date') or '')} | {r['in_time']} | {r['out_time']} | "
            f"{r['total_time']} | {r['project']} | {_task_cell_text(r)}"
        )
    rows_text = "\n".join(table_lines)
    text_body = (
        "GREETINGS:\n"
        "Respected TL/PM/HR,\n\n"
        f"{rows_text}\n\n"
        f"Total: {_format_hhmm(total_seconds)}h\n\n"
        "Regards,\n"
        f"{user_name}"
    )

    return {
        "source": "github_local_timer",
        "date": str(target_day),
        "subject": subject,
        "to": to_list,
        "cc": cc_list,
        "rows": rows,
        "sessions": sessions,
        "commit_count": sum(int(r.get("commit_count") or 0) for r in rows),
        "session_count": len(rows),
        "total_seconds": total_seconds,
        "total_time": _format_hhmm(total_seconds),
        "html": html,
        "text_body": text_body,
        "rows_text": rows_text,
        "user_name": user_name,
        "tz": tz_name,
        "smtp_ready": bool(
            (os.getenv("SMTP_HOST") or "").strip()
            and (os.getenv("SMTP_USER") or "").strip()
        ),
    }
