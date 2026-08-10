"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import EmailRecipientFields from "./EmailRecipientFields";
import {
  emailListPayload,
  loadEmailRecipients,
} from "../lib/emailRecipients";

import { API } from "../lib/api";
const LOCAL_KEY = "github-daily-local-timer-v1";

type ActiveTimer = {
  owner_key: string;
  repo_path: string;
  branch: string;
  project: string;
  full_name?: string;
  started_at: string;
};

type SavedSession = {
  id: string;
  repo_path: string;
  branch: string;
  project: string;
  started_at: string;
  ended_at: string;
  seconds: number;
  commit_count?: number;
  commits?: Array<{ short_sha?: string; message?: string }>;
};

type LocalPreview = {
  subject: string;
  to: string[];
  cc: string[];
  html: string;
  text_body: string;
  total_time: string;
  date: string;
  session_count?: number;
  commit_count?: number;
  rows: Array<{
    sr: number;
    date: string;
    in_time: string;
    out_time: string;
    total_time: string;
    project: string;
    task_summary: string;
  }>;
};

function todayISO() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function formatDuration(sec: number) {
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
  return `${m}:${String(r).padStart(2, "0")}`;
}

function loadLocalPrefs() {
  try {
    const raw = localStorage.getItem(LOCAL_KEY);
    if (!raw) return { ownerKey: "", repoPath: "", branch: "" };
    return JSON.parse(raw) as {
      ownerKey: string;
      repoPath: string;
      branch: string;
    };
  } catch {
    return { ownerKey: "", repoPath: "", branch: "" };
  }
}

function saveLocalPrefs(p: {
  ownerKey: string;
  repoPath: string;
  branch: string;
}) {
  localStorage.setItem(LOCAL_KEY, JSON.stringify(p));
}

function ensureOwnerKey(existing: string) {
  if (existing.trim()) return existing.trim();
  const id =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `local-${Date.now()}`;
  return id;
}

function sessionHasCommits(s: SavedSession) {
  const linked = (s.commits || []).filter(
    (c) => c.short_sha || c.message
  ).length;
  return linked > 0 || (s.commit_count || 0) > 0;
}

export default function GitHubLocalTimer({
  displayName,
  githubSessionId,
}: {
  displayName?: string;
  githubSessionId?: string | null;
}) {
  const prefs = useMemo(() => loadLocalPrefs(), []);
  const [ownerKey, setOwnerKey] = useState(() =>
    ensureOwnerKey(prefs.ownerKey || "")
  );
  const [repoPath, setRepoPath] = useState(prefs.repoPath || "");
  const [branch, setBranch] = useState(prefs.branch || "");
  const [active, setActive] = useState<ActiveTimer | null>(null);
  const [sessions, setSessions] = useState<SavedSession[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [commitMessage, setCommitMessage] = useState("");
  const [pushToGithub, setPushToGithub] = useState(true);
  const [lastCommit, setLastCommit] = useState<string | null>(null);
  const [lastPush, setLastPush] = useState<string | null>(null);
  const [date, setDate] = useState(todayISO());
  const [emailTo, setEmailTo] = useState("");
  const [emailCc, setEmailCc] = useState("");
  const [preview, setPreview] = useState<LocalPreview | null>(null);
  const [emailOpen, setEmailOpen] = useState(false);

  const committedSessions = useMemo(
    () => sessions.filter(sessionHasCommits),
    [sessions]
  );

  const persistPrefs = useCallback(
    (next?: Partial<{ ownerKey: string; repoPath: string; branch: string }>) => {
      const payload = {
        ownerKey: next?.ownerKey ?? ownerKey,
        repoPath: next?.repoPath ?? repoPath,
        branch: next?.branch ?? branch,
      };
      saveLocalPrefs(payload);
    },
    [ownerKey, repoPath, branch]
  );

  const refresh = useCallback(async () => {
    const key = ensureOwnerKey(ownerKey);
    if (key !== ownerKey) {
      setOwnerKey(key);
      persistPrefs({ ownerKey: key });
    }
    const res = await axios.get(`${API}/reports/github-daily/local/timer`, {
      params: { owner_key: key, date },
    });
    setActive(res.data.active || null);
    setSessions(res.data.sessions || []);
  }, [ownerKey, date, persistPrefs]);

  useEffect(() => {
    const saved = loadEmailRecipients();
    setEmailTo(saved.to);
    setEmailCc(saved.cc);
  }, []);

  useEffect(() => {
    const key = ensureOwnerKey(ownerKey);
    if (key !== ownerKey) {
      setOwnerKey(key);
      saveLocalPrefs({ ownerKey: key, repoPath, branch });
    }
    void refresh().catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void refresh().catch((err) => {
      setError(err?.response?.data?.detail || err?.message || "Could not load timers");
    });
  }, [date, refresh]);

  useEffect(() => {
    if (!active?.started_at) {
      setElapsed(0);
      return;
    }
    const tick = () => {
      const start = Date.parse(active.started_at);
      setElapsed(Math.max(0, Math.floor((Date.now() - start) / 1000)));
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [active]);

  const inspect = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await axios.post(`${API}/reports/github-daily/local/inspect`, {
        repo_path: repoPath.trim(),
      });
      setBranch(res.data.branch || "");
      persistPrefs({ branch: res.data.branch || "" });
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || "Invalid repo path");
    } finally {
      setBusy(false);
    }
  };

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const key = ensureOwnerKey(ownerKey);
      setOwnerKey(key);
      persistPrefs({ ownerKey: key });
      const res = await axios.post(
        `${API}/reports/github-daily/local/timer/start`,
        {
          owner_key: key,
          repo_path: repoPath.trim(),
          branch: branch.trim() || undefined,
          display_name: displayName || undefined,
        }
      );
      setActive(res.data.active);
      persistPrefs();
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || "Could not start timer");
    } finally {
      setBusy(false);
    }
  };

  const ghHeaders = () => {
    const h: Record<string, string> = {};
    if (githubSessionId) h["X-Github-Session"] = githubSessionId;
    return h;
  };

  const notePush = (
    pushed: {
      full_name?: string;
      branch?: string;
      up_to_date?: boolean;
      dirty?: boolean;
      changed_files?: number;
      branch_created_local?: boolean;
      branch_created_remote?: boolean;
    } | null
  ) => {
    if (!pushed) {
      setLastPush(null);
      return;
    }
    const dest = pushed.full_name
      ? `${pushed.full_name}@${pushed.branch || ""}`
      : pushed.branch || "origin";
    let msg = pushed.up_to_date
      ? `Already up to date on GitHub · ${dest}`
      : pushed.branch_created_local
        ? `Created branch & pushed to GitHub · ${dest}`
        : `Pushed to GitHub · ${dest}`;
    if (pushed.dirty) {
      msg += ` · ${pushed.changed_files || "some"} uncommitted file(s) still local`;
    }
    setLastPush(msg);
  };

  const stop = async (withCommit: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const payload: Record<string, unknown> = { owner_key: ownerKey };
      if (withCommit) {
        const msg = commitMessage.trim();
        if (!msg) {
          setError("Enter a commit message, or use Stop & save without commit.");
          setBusy(false);
          return;
        }
        payload.commit_message = msg;
        payload.add_all = true;
        payload.push = pushToGithub;
      }
      const res = await axios.post(
        `${API}/reports/github-daily/local/timer/stop`,
        payload,
        { headers: ghHeaders() }
      );
      if (res.data.created_commit?.short_sha) {
        setLastCommit(
          `${res.data.created_commit.short_sha} ${res.data.created_commit.message}`
        );
      } else {
        setLastCommit(null);
      }
      notePush(res.data.pushed || null);
      setCommitMessage("");
      setActive(null);
      await refresh();
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || "Could not stop timer");
    } finally {
      setBusy(false);
    }
  };

  const commitNow = async () => {
    const msg = commitMessage.trim();
    if (!msg) {
      setError("Enter a commit message.");
      return;
    }
    if (pushToGithub && !githubSessionId) {
      setError("Connect GitHub below first, then commit & push.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await axios.post(
        `${API}/reports/github-daily/local/commit`,
        {
          owner_key: ownerKey,
          repo_path: repoPath.trim(),
          branch: branch.trim() || undefined,
          message: msg,
          add_all: true,
          push: pushToGithub,
        },
        { headers: ghHeaders() }
      );
      setLastCommit(
        `${res.data.commit.short_sha} ${res.data.commit.message}`
      );
      notePush(res.data.pushed || null);
      setCommitMessage("");
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || "Commit failed");
    } finally {
      setBusy(false);
    }
  };

  const pushOnly = async () => {
    if (!githubSessionId) {
      setError("Connect GitHub below first, then push.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await axios.post(
        `${API}/reports/github-daily/local/push`,
        {
          owner_key: ownerKey,
          repo_path: repoPath.trim(),
          branch: branch.trim() || undefined,
        },
        { headers: ghHeaders() }
      );
      notePush(res.data.pushed || null);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || "Push failed");
    } finally {
      setBusy(false);
    }
  };

  const discard = async () => {
    setBusy(true);
    setError(null);
    try {
      await axios.post(`${API}/reports/github-daily/local/timer/discard`, {
        owner_key: ownerKey,
      });
      setActive(null);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || "Could not discard");
    } finally {
      setBusy(false);
    }
  };

  const loadSheet = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await axios.post(
        `${API}/reports/github-daily-email/preview-local`,
        {
          owner_key: ownerKey,
          date,
          display_name: displayName || undefined,
          ...emailListPayload(emailTo, emailCc),
        }
      );
      setPreview(res.data);
      setEmailOpen(true);
    } catch (err: any) {
      setError(
        err?.response?.data?.detail ||
          err?.message ||
          "Could not build local timer sheet"
      );
    } finally {
      setBusy(false);
    }
  };

  const copyAndOpenMail = async () => {
    if (!preview) return;
    try {
      const html = preview.html || "";
      const text = preview.text_body || "";
      if (navigator.clipboard && "write" in navigator.clipboard && html) {
        await navigator.clipboard.write([
          new ClipboardItem({
            "text/html": new Blob([html], { type: "text/html" }),
            "text/plain": new Blob([text], { type: "text/plain" }),
          }),
        ]);
      }
      await axios.post(`${API}/reports/github-daily-email/open-mail-local`, {
        owner_key: ownerKey,
        date,
        display_name: displayName || undefined,
        ...emailListPayload(emailTo, emailCc),
      });
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || "Could not open Mail");
    }
  };

  const openGmail = async () => {
    if (!preview) return;
    try {
      const html = preview.html || "";
      const text = preview.text_body || "";
      if (navigator.clipboard && "write" in navigator.clipboard && html) {
        await navigator.clipboard.write([
          new ClipboardItem({
            "text/html": new Blob([html], { type: "text/html" }),
            "text/plain": new Blob([text], { type: "text/plain" }),
          }),
        ]);
      }
      const params = new URLSearchParams();
      params.set("view", "cm");
      params.set("fs", "1");
      if (preview.to.length) params.set("to", preview.to.join(","));
      if (preview.cc.length) params.set("cc", preview.cc.join(","));
      params.set("su", preview.subject);
      window.open(
        `https://mail.google.com/mail/?${params.toString()}`,
        "_blank",
        "noopener,noreferrer"
      );
      alert("Gmail opened. Press Cmd+V to paste the sheet once.");
    } catch (err: any) {
      setError(err?.message || "Could not open Gmail");
    }
  };

  const commitLabel = pushToGithub ? "Commit & push" : "Commit";
  const commitStopLabel = pushToGithub
    ? "Commit, push & stop"
    : "Commit & stop";

  return (
    <div className="mb-6 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="border-b border-slate-100 bg-gradient-to-br from-emerald-50/80 via-white to-slate-50 px-6 py-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-emerald-700">
              Local branch timer
            </p>
            <h3 className="mt-1 text-xl font-bold tracking-tight text-slate-900">
              Time + commits (local &amp; GitHub)
            </h3>
            <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-slate-600">
              Start a timer on a local branch, commit from the app, and push to
              GitHub so work lands on the remote — not only your machine.
            </p>
          </div>
          {active && (
            <div className="rounded-full border border-emerald-300 bg-emerald-100 px-3 py-1 text-xs font-semibold text-emerald-900">
              ● Live · {formatDuration(elapsed)}
            </div>
          )}
        </div>
      </div>

      <div className="space-y-4 px-6 py-5">
        <div className="grid gap-3 lg:grid-cols-[1fr_auto]">
          <label className="block text-xs font-medium uppercase tracking-wide text-slate-500">
            Local repo path
            <input
              type="text"
              value={repoPath}
              onChange={(e) => {
                setRepoPath(e.target.value);
                persistPrefs({ repoPath: e.target.value });
              }}
              placeholder="/Users/you/projects/my-app"
              className="mt-1.5 block w-full rounded-lg border border-slate-300 bg-slate-50/50 px-3 py-2.5 font-mono text-sm text-slate-900 outline-none transition focus:border-emerald-500 focus:bg-white focus:ring-2 focus:ring-emerald-100"
            />
          </label>
        </div>

        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <label className="block min-w-0 flex-1 text-xs font-medium uppercase tracking-wide text-slate-500">
            Branch
            <input
              type="text"
              value={branch}
              onChange={(e) => {
                setBranch(e.target.value);
                persistPrefs({ branch: e.target.value });
              }}
              placeholder="feature/…"
              className="mt-1.5 block w-full rounded-lg border border-slate-300 bg-slate-50/50 px-3 py-2.5 font-mono text-sm text-slate-900 outline-none transition focus:border-emerald-500 focus:bg-white focus:ring-2 focus:ring-emerald-100"
            />
          </label>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={inspect}
              disabled={busy || !repoPath.trim()}
              className="rounded-lg border border-slate-300 bg-white px-3.5 py-2.5 text-sm font-semibold text-slate-700 shadow-sm hover:bg-slate-50 disabled:opacity-60"
            >
              Detect branch
            </button>
            {!active ? (
              <button
                type="button"
                onClick={start}
                disabled={busy || !repoPath.trim()}
                className="rounded-lg bg-emerald-700 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-emerald-800 disabled:opacity-60"
              >
                Start timer
              </button>
            ) : (
              <>
                <button
                  type="button"
                  onClick={() => stop(false)}
                  disabled={busy}
                  className="rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-slate-800 disabled:opacity-60"
                >
                  Stop & save
                </button>
                <button
                  type="button"
                  onClick={discard}
                  disabled={busy}
                  className="rounded-lg border border-slate-300 bg-white px-3.5 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-60"
                >
                  Discard
                </button>
              </>
            )}
          </div>
        </div>

        {active && (
          <div className="rounded-2xl border border-emerald-200 bg-gradient-to-br from-emerald-50 to-teal-50/40 p-4 shadow-inner">
            <div className="mb-4 flex flex-wrap items-start justify-between gap-2 border-b border-emerald-200/70 pb-3">
              <div>
                <p className="text-base font-semibold text-emerald-950">
                  Running · {formatDuration(elapsed)} ·{" "}
                  <span className="font-mono">{active.branch}</span>
                </p>
                <p className="mt-0.5 font-mono text-xs text-emerald-800/70">
                  {active.repo_path}
                </p>
              </div>
              {active.project && (
                <span className="rounded-md border border-emerald-200 bg-white/80 px-2 py-1 text-xs font-medium text-emerald-900">
                  {active.project}
                </span>
              )}
            </div>

            <label className="block text-xs font-medium uppercase tracking-wide text-emerald-900/70">
              Commit message
              <input
                type="text"
                value={commitMessage}
                onChange={(e) => setCommitMessage(e.target.value)}
                placeholder="e.g. NMAD-123 fix login validation"
                className="mt-1.5 block w-full rounded-lg border border-emerald-300 bg-white px-3 py-2.5 text-sm text-slate-900 outline-none transition focus:border-emerald-500 focus:ring-2 focus:ring-emerald-200"
              />
            </label>

            <label className="mt-3 flex items-center gap-2.5 text-sm text-emerald-950">
              <input
                type="checkbox"
                checked={pushToGithub}
                onChange={(e) => setPushToGithub(e.target.checked)}
                className="h-4 w-4 rounded border-emerald-400 text-emerald-700 focus:ring-emerald-500"
              />
              Push to GitHub after commit
            </label>

            {pushToGithub && !githubSessionId && (
              <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                Connect GitHub in the section below so push can authenticate.
              </p>
            )}

            <div className="mt-4 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={commitNow}
                disabled={busy || !commitMessage.trim()}
                className="rounded-lg bg-emerald-700 px-3.5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-emerald-800 disabled:opacity-60"
              >
                {commitLabel}
              </button>
              <button
                type="button"
                onClick={() => stop(true)}
                disabled={busy || !commitMessage.trim()}
                className="rounded-lg border border-emerald-600 bg-white px-3.5 py-2.5 text-sm font-semibold text-emerald-800 hover:bg-emerald-50 disabled:opacity-60"
              >
                {commitStopLabel}
              </button>
              <button
                type="button"
                onClick={pushOnly}
                disabled={busy || !githubSessionId}
                className="rounded-lg border border-slate-300 bg-white px-3.5 py-2.5 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-60"
              >
                Push only
              </button>
            </div>

            <p className="mt-3 text-xs leading-relaxed text-emerald-800/75">
              Stages all changes (<code className="rounded bg-white/70 px-1">git add -A</code>
              ), commits, then pushes the branch to origin on GitHub when enabled.
            </p>

            {(lastCommit || lastPush) && (
              <div className="mt-3 space-y-1.5">
                {lastCommit && (
                  <div className="flex items-start gap-2 rounded-lg border border-emerald-200 bg-white px-3 py-2 text-xs text-emerald-950">
                    <span className="mt-0.5 shrink-0 rounded bg-emerald-100 px-1.5 py-0.5 font-semibold uppercase tracking-wide text-emerald-800">
                      Commit
                    </span>
                    <span className="min-w-0 break-all font-mono">{lastCommit}</span>
                  </div>
                )}
                {lastPush && (
                  <div className="flex items-start gap-2 rounded-lg border border-emerald-200 bg-white px-3 py-2 text-xs text-emerald-950">
                    <span className="mt-0.5 shrink-0 rounded bg-sky-100 px-1.5 py-0.5 font-semibold uppercase tracking-wide text-sky-800">
                      Push
                    </span>
                    <span className="min-w-0 break-all">{lastPush}</span>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {!active && (lastCommit || lastPush) && (
          <div className="space-y-1.5 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5">
            {lastCommit && (
              <p className="text-xs text-slate-700">
                <span className="font-semibold text-slate-900">Last commit:</span>{" "}
                <span className="font-mono">{lastCommit}</span>
              </p>
            )}
            {lastPush && (
              <p className="text-xs text-slate-700">
                <span className="font-semibold text-slate-900">Last push:</span>{" "}
                {lastPush}
              </p>
            )}
          </div>
        )}

        <div className="flex flex-wrap items-end gap-3 border-t border-slate-100 pt-4">
          <EmailRecipientFields
            to={emailTo}
            cc={emailCc}
            onChange={({ to, cc }) => {
              setEmailTo(to);
              setEmailCc(cc);
            }}
            className="w-full"
          />
          <label className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Date
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="mt-1.5 block rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100"
            />
          </label>
          <button
            type="button"
            onClick={loadSheet}
            disabled={busy || committedSessions.length === 0}
            className="rounded-lg bg-emerald-800 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-emerald-900 disabled:opacity-60"
          >
            Preview sheet from local timers
          </button>
          <button
            type="button"
            onClick={() => refresh()}
            disabled={busy}
            className="rounded-lg border border-slate-300 bg-white px-3.5 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Refresh sessions
          </button>
          {sessions.length > 0 && (
            <p className="ml-auto text-xs text-slate-500">
              Showing {committedSessions.length} with commits
              {sessions.length !== committedSessions.length
                ? ` · ${sessions.length - committedSessions.length} omitted (no commits)`
                : ""}
            </p>
          )}
        </div>

        {committedSessions.length > 0 && (
          <ul className="overflow-hidden rounded-xl border border-slate-200">
            {committedSessions.map((s) => (
              <li
                key={s.id}
                className="border-b border-slate-100 px-4 py-3 last:border-b-0 hover:bg-slate-50/80"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-sm font-semibold text-slate-900">
                    {s.project}
                  </p>
                  <span className="rounded-md bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-slate-600">
                    {s.branch}
                  </span>
                  <span className="text-xs font-medium text-emerald-700">
                    {formatDuration(s.seconds)}
                  </span>
                  <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-800">
                    {s.commit_count || (s.commits || []).length} commit
                    {(s.commit_count || (s.commits || []).length) === 1
                      ? ""
                      : "s"}
                  </span>
                </div>
                <div className="mt-1.5 space-y-0.5">
                  {(s.commits || []).slice(0, 3).map((c, i) => (
                    <p
                      key={i}
                      className="truncate font-mono text-xs text-slate-500"
                    >
                      <span className="text-emerald-700">{c.short_sha}</span>{" "}
                      {c.message}
                    </p>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        )}

        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
            {error}
          </div>
        )}
      </div>

      {emailOpen && preview && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="max-h-[90vh] w-full max-w-3xl overflow-hidden rounded-2xl bg-white shadow-xl">
            <div className="flex items-start justify-between border-b border-slate-200 px-5 py-4">
              <div>
                <h4 className="font-semibold text-slate-900">{preview.subject}</h4>
                <p className="text-xs text-slate-600">
                  {preview.session_count || 0} sessions with commits · Total{" "}
                  {preview.total_time}h
                </p>
              </div>
              <button
                type="button"
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
                onClick={() => setEmailOpen(false)}
              >
                Close
              </button>
            </div>
            <div
              className="max-h-[55vh] overflow-auto p-5"
              dangerouslySetInnerHTML={{ __html: preview.html }}
            />
            <div className="flex flex-wrap justify-end gap-2 border-t border-slate-200 px-5 py-4">
              <button
                type="button"
                onClick={copyAndOpenMail}
                className="rounded-md bg-emerald-700 px-3 py-2 text-sm font-semibold text-white"
              >
                Open in Mac Mail
              </button>
              <button
                type="button"
                onClick={openGmail}
                className="rounded-md border border-slate-300 px-3 py-2 text-sm font-semibold"
              >
                Open Gmail draft
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
