"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import GitHubLocalTimer from "./GitHubLocalTimer";
import EmailRecipientFields from "./EmailRecipientFields";
import {
  emailListPayload,
  loadEmailRecipients,
} from "../lib/emailRecipients";

const API = "http://127.0.0.1:8000";
const STORAGE_KEY = "github-daily-connection-v3";

type GithubProject = {
  full_name: string;
  name: string;
  owner: string;
  private: boolean;
  html_url: string;
  description?: string;
};

type StoredConnection = {
  sessionId: string;
  username: string;
  displayName: string;
  selectedRepos: string[];
};

type EmailPreview = {
  subject: string;
  to: string[];
  cc: string[];
  html: string;
  text_body: string;
  total_time: string;
  commit_count: number;
  session_count?: number;
  used_local_timers?: boolean;
  date: string;
  user_name?: string;
  rows: Array<{
    sr: number;
    date: string;
    in_time: string;
    out_time: string;
    total_time: string;
    project: string;
    task_summary: string;
    task_url?: string | null;
    task_commits?: Array<{
      summary: string;
      url?: string;
      short_sha?: string;
    }>;
    sha?: string;
    source?: string;
  }>;
};

function todayISO() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function loadStored(): StoredConnection | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredConnection;
    if (!parsed?.sessionId || !parsed?.username) return null;
    return {
      ...parsed,
      selectedRepos: Array.isArray(parsed.selectedRepos)
        ? parsed.selectedRepos
        : [],
    };
  } catch {
    return null;
  }
}

function saveStored(conn: StoredConnection) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(conn));
}

function clearStored() {
  localStorage.removeItem(STORAGE_KEY);
}

function ghHeaders(sessionId: string | null) {
  const h: Record<string, string> = {};
  if (sessionId) h["X-Github-Session"] = sessionId;
  return h;
}

export default function GitHubDailyBoard() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [connected, setConnected] = useState(false);
  const [oauthConfigured, setOauthConfigured] = useState(false);
  const [oauthRedirect, setOauthRedirect] = useState("");

  const [projects, setProjects] = useState<GithubProject[]>([]);
  const [selectedRepos, setSelectedRepos] = useState<string[]>([]);
  const [projectFilter, setProjectFilter] = useState("");
  const [loadingProjects, setLoadingProjects] = useState(false);
  const [orgHint, setOrgHint] = useState<string | null>(null);
  const [orgNames, setOrgNames] = useState<string[]>([]);

  const [showPat, setShowPat] = useState(false);
  const [pat, setPat] = useState("");
  const [connectingPat, setConnectingPat] = useState(false);

  const [date, setDate] = useState(todayISO());
  const [emailTo, setEmailTo] = useState("");
  const [emailCc, setEmailCc] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<EmailPreview | null>(null);
  const [emailOpen, setEmailOpen] = useState(false);

  const filteredProjects = useMemo(() => {
    const q = projectFilter.trim().toLowerCase();
    if (!q) return projects;
    return projects.filter(
      (p) =>
        p.full_name.toLowerCase().includes(q) ||
        (p.description || "").toLowerCase().includes(q)
    );
  }, [projects, projectFilter]);

  const previewRows = useMemo(() => {
    if (!preview?.rows) return [];
    return preview.rows.filter(
      (r) =>
        !String(r.task_summary || "")
          .toLowerCase()
          .includes("no commits linked") &&
        !String(r.task_summary || "")
          .toLowerCase()
          .includes("no commits during")
    );
  }, [preview]);

  const persist = useCallback(
    (
      nextSelected: string[] = selectedRepos,
      nextUser = username,
      nextName = displayName,
      nextSid = sessionId
    ) => {
      if (!nextSid || !nextUser) return;
      saveStored({
        sessionId: nextSid,
        username: nextUser,
        displayName: nextName || nextUser,
        selectedRepos: nextSelected,
      });
    },
    [selectedRepos, username, displayName, sessionId]
  );

  const refreshProjects = useCallback(
    async (sid: string, keepSelected?: string[]) => {
      setLoadingProjects(true);
      try {
        const res = await axios.post(
          `${API}/reports/github-daily/projects`,
          {},
          { headers: ghHeaders(sid) }
        );
        const list: GithubProject[] = res.data.projects || [];
        setProjects(list);
        setOrgHint(res.data.hint || null);
        setOrgNames(
          (res.data.orgs || []).map((o: { login: string }) => o.login).filter(Boolean)
        );
        const names = new Set(list.map((p) => p.full_name));
        const prev = keepSelected ?? selectedRepos;
        const stillValid = prev.filter((r) => names.has(r));
        setSelectedRepos(stillValid);
        return list;
      } finally {
        setLoadingProjects(false);
      }
    },
    [selectedRepos]
  );

  const applyConnected = useCallback(
    async (
      sid: string,
      user: string,
      name: string,
      keepSelected?: string[]
    ) => {
      setSessionId(sid);
      setUsername(user);
      setDisplayName(name || user);
      setConnected(true);
      setPreview(null);
      const kept = keepSelected || [];
      setSelectedRepos(kept);
      saveStored({
        sessionId: sid,
        username: user,
        displayName: name || user,
        selectedRepos: kept,
      });
      await refreshProjects(sid, kept);
    },
    [refreshProjects]
  );

  useEffect(() => {
    const saved = loadEmailRecipients();
    setEmailTo(saved.to);
    setEmailCc(saved.cc);
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const err = params.get("github_error");
    if (err) setError(err);

    const ghSid = params.get("gh_sid");
    const connectedFlag = params.get("github") === "connected";

    axios
      .get(`${API}/auth/github/setup`)
      .then((res) => {
        setOauthConfigured(!!res.data.oauth_configured);
        setOauthRedirect(res.data.redirect_uri || "");
      })
      .catch(() => setOauthConfigured(false));

    const boot = async () => {
      const sid = ghSid || loadStored()?.sessionId || null;
      const stored = loadStored();
      if (!sid) return;
      try {
        const res = await axios.get(`${API}/auth/github/status`, {
          headers: ghHeaders(sid),
        });
        if (!res.data.connected) {
          if (ghSid) setError("GitHub session expired. Connect again.");
          clearStored();
          return;
        }
        await applyConnected(
          sid,
          res.data.username || stored?.username || "",
          res.data.user_name || stored?.displayName || res.data.username || "",
          stored?.selectedRepos || []
        );
        if (connectedFlag || ghSid) {
          const url = new URL(window.location.href);
          url.searchParams.delete("gh_sid");
          url.searchParams.delete("github");
          url.searchParams.delete("github_error");
          url.searchParams.set("tab", "github");
          window.history.replaceState({}, "", url.toString());
        }
      } catch {
        /* ignore */
      }
    };
    void boot();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const disconnect = async () => {
    try {
      if (sessionId) {
        await axios.post(
          `${API}/auth/github/logout`,
          {},
          { headers: ghHeaders(sessionId) }
        );
      }
    } catch {
      /* ignore */
    }
    clearStored();
    setConnected(false);
    setSessionId(null);
    setUsername("");
    setDisplayName("");
    setProjects([]);
    setSelectedRepos([]);
    setPreview(null);
    setPat("");
    setError(null);
  };

  const connectWithPat = async () => {
    if (!pat.trim()) {
      setError("Paste a PAT, or use Connect with GitHub instead.");
      return;
    }
    setConnectingPat(true);
    setError(null);
    try {
      const res = await axios.post(`${API}/reports/github-daily/connect`, {
        github_token: pat.trim(),
      });
      await applyConnected(
        res.data.session_id,
        res.data.username,
        res.data.name || res.data.username,
        selectedRepos
      );
      setPat("");
      setShowPat(false);
    } catch (err: any) {
      setError(
        err?.response?.data?.detail ||
          err?.message ||
          "Could not connect with PAT"
      );
    } finally {
      setConnectingPat(false);
    }
  };

  const toggleRepo = (fullName: string) => {
    setSelectedRepos((prev) => {
      const next = prev.includes(fullName)
        ? prev.filter((x) => x !== fullName)
        : [...prev, fullName];
      persist(next);
      return next;
    });
  };

  const selectAllFiltered = () => {
    const names = filteredProjects.map((p) => p.full_name);
    const next = Array.from(new Set([...selectedRepos, ...names]));
    setSelectedRepos(next);
    persist(next);
  };

  const clearSelected = () => {
    setSelectedRepos([]);
    persist([]);
  };

  const loadPreview = async () => {
    if (!sessionId) {
      setError("Connect with GitHub first.");
      return;
    }
    if (!selectedRepos.length) {
      setError("Select at least one project you are connected to.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await axios.post(
        `${API}/reports/github-daily-email/preview`,
        {
          date,
          github_repos: selectedRepos.join(","),
          display_name: displayName || username,
          github_username: username,
          ...emailListPayload(emailTo, emailCc),
        },
        { headers: ghHeaders(sessionId) }
      );
      setPreview(res.data);
      persist();
    } catch (err: any) {
      setPreview(null);
      setError(
        err?.response?.data?.detail ||
          err?.message ||
          "Could not load GitHub commits"
      );
    } finally {
      setLoading(false);
    }
  };

  const copyTemplate = async () => {
    if (!preview) return;
    const html = preview.html || "";
    const text = preview.text_body || "";
    if (navigator.clipboard && "write" in navigator.clipboard && html) {
      const item = new ClipboardItem({
        "text/html": new Blob([html], { type: "text/html" }),
        "text/plain": new Blob([text], { type: "text/plain" }),
      });
      await navigator.clipboard.write([item]);
    } else {
      await navigator.clipboard.writeText(text);
    }
  };

  const openMacMail = async () => {
    if (!preview || !sessionId) return;
    setError(null);
    try {
      await copyTemplate();
      await axios.post(
        `${API}/reports/github-daily-email/open-mail-app`,
        {
          date,
          github_repos: selectedRepos.join(","),
          display_name: displayName || username,
          github_username: username,
          ...emailListPayload(emailTo, emailCc),
        },
        { headers: ghHeaders(sessionId) }
      );
    } catch (err: any) {
      setError(
        err?.response?.data?.detail ||
          err?.message ||
          "Could not open Apple Mail"
      );
    }
  };

  const openGmail = async () => {
    if (!preview) return;
    setError(null);
    try {
      await copyTemplate();
      const params = new URLSearchParams();
      params.set("view", "cm");
      params.set("fs", "1");
      params.set("tf", "1");
      if (preview.to.length) params.set("to", preview.to.join(","));
      if (preview.cc.length) params.set("cc", preview.cc.join(","));
      params.set("su", preview.subject);
      window.open(
        `https://mail.google.com/mail/?${params.toString()}`,
        "_blank",
        "noopener,noreferrer"
      );
      alert(
        "Gmail opened with To/Subject. Press Cmd+V in the body to paste the template once."
      );
    } catch (err: any) {
      setError(err?.message || "Could not open Gmail");
    }
  };

  return (
    <div className="mt-2">
      <GitHubLocalTimer
        displayName={displayName || username}
        githubSessionId={sessionId}
      />

      <div className="mb-6 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-100 bg-gradient-to-br from-slate-50 via-white to-emerald-50/40 px-6 py-5">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-600">
                GitHub Daily · projects
              </p>
              <h2 className="mt-1 text-2xl font-bold tracking-tight text-slate-900">
                Commits from connected projects
              </h2>
              <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-slate-600">
                Build the same daily sheet from commits when you didn&apos;t run
                Jira timers — ready for TL/PM/HR. Connect GitHub, select
                projects, load today&apos;s commits, then email.
              </p>
            </div>
            <p className="shrink-0">
              {connected ? (
                <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-800">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                  Connected as {username}
                </span>
              ) : (
                <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-800">
                  Not connected
                </span>
              )}
            </p>
          </div>
        </div>
        <div className="px-6 py-5">

        {!connected ? (
          <div className="space-y-4">
            {oauthConfigured ? (
              <a
                href={`${API}/auth/github/login`}
                className="inline-flex rounded-md bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white hover:bg-slate-800"
              >
                Connect with GitHub
              </a>
            ) : (
              <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">
                <p className="font-semibold">GitHub OAuth not configured yet</p>
                <p className="mt-1">
                  Admin: create an OAuth App at{" "}
                  <a
                    href="https://github.com/settings/developers"
                    target="_blank"
                    rel="noreferrer"
                    className="underline"
                  >
                    github.com/settings/developers
                  </a>
                  , set callback to{" "}
                  <code className="rounded bg-white px-1">
                    {oauthRedirect ||
                      "http://localhost:8000/auth/github/callback"}
                  </code>
                  , then add <code className="rounded bg-white px-1">GITHUB_CLIENT_ID</code>{" "}
                  and{" "}
                  <code className="rounded bg-white px-1">GITHUB_CLIENT_SECRET</code>{" "}
                  to <code className="rounded bg-white px-1">backend/.env</code>.
                </p>
              </div>
            )}

            <div>
              <button
                type="button"
                onClick={() => setShowPat((v) => !v)}
                className="text-sm font-medium text-slate-600 underline"
              >
                {showPat
                  ? "Hide PAT fallback"
                  : "Use personal access token instead (optional)"}
              </button>
              {showPat && (
                <div className="mt-3 max-w-xl space-y-2">
                  <input
                    type="password"
                    value={pat}
                    onChange={(e) => setPat(e.target.value)}
                    placeholder="ghp_…"
                    autoComplete="off"
                    className="block w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                  />
                  <button
                    type="button"
                    onClick={connectWithPat}
                    disabled={connectingPat || !pat.trim()}
                    className="rounded-md border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-50 disabled:opacity-60"
                  >
                    {connectingPat ? "Connecting…" : "Connect with PAT"}
                  </button>
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-end gap-3">
              <label className="text-sm text-slate-700">
                Display name (email signature)
                <input
                  type="text"
                  value={displayName}
                  onChange={(e) => {
                    setDisplayName(e.target.value);
                    persist(selectedRepos, username, e.target.value);
                  }}
                  className="mt-1 block w-64 rounded-md border border-slate-300 px-3 py-2 text-sm"
                />
              </label>
              <button
                type="button"
                onClick={() => sessionId && refreshProjects(sessionId)}
                disabled={loadingProjects || !sessionId}
                className="rounded-md border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-60"
              >
                {loadingProjects ? "Refreshing…" : "Refresh projects"}
              </button>
              <button
                type="button"
                onClick={disconnect}
                className="rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-700 hover:bg-slate-50"
              >
                Disconnect
              </button>
            </div>

            <div>
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-semibold text-slate-800">
                  Your GitHub projects ({projects.length}) · selected{" "}
                  {selectedRepos.length}
                  {orgNames.length > 0
                    ? ` · orgs: ${orgNames.join(", ")}`
                    : ""}
                </p>
                <div className="flex flex-wrap gap-2">
                  <input
                    type="search"
                    value={projectFilter}
                    onChange={(e) => setProjectFilter(e.target.value)}
                    placeholder="Filter projects…"
                    className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
                  />
                  <button
                    type="button"
                    onClick={selectAllFiltered}
                    className="rounded-md border border-slate-300 px-2 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
                  >
                    Select filtered
                  </button>
                  <button
                    type="button"
                    onClick={clearSelected}
                    className="rounded-md border border-slate-300 px-2 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
                  >
                    Clear
                  </button>
                </div>
              </div>

              {orgHint && (
                <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-950">
                  {orgHint} Then click <strong>Refresh projects</strong>.
                </div>
              )}

              <div className="max-h-64 overflow-auto rounded-xl border border-slate-200">
                {filteredProjects.length === 0 ? (
                  <p className="p-4 text-sm text-slate-500">
                    {loadingProjects
                      ? "Loading projects…"
                      : "No projects found for this account."}
                  </p>
                ) : (
                  <ul className="divide-y divide-slate-100">
                    {filteredProjects.map((p) => {
                      const checked = selectedRepos.includes(p.full_name);
                      return (
                        <li key={p.full_name}>
                          <label className="flex cursor-pointer items-start gap-3 px-3 py-2 hover:bg-slate-50">
                            <input
                              type="checkbox"
                              className="mt-1"
                              checked={checked}
                              onChange={() => toggleRepo(p.full_name)}
                            />
                            <span className="min-w-0 flex-1">
                              <span className="block text-sm font-medium text-slate-900">
                                {p.full_name}
                                {p.private ? (
                                  <span className="ml-2 rounded bg-slate-200 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-slate-600">
                                    private
                                  </span>
                                ) : null}
                              </span>
                              {p.description ? (
                                <span className="block truncate text-xs text-slate-500">
                                  {p.description}
                                </span>
                              ) : null}
                            </span>
                          </label>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </div>

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
              <label className="text-sm text-slate-700">
                Date
                <input
                  type="date"
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                  className="mt-1 block rounded-md border border-slate-300 px-3 py-2 text-sm"
                />
              </label>
              <button
                type="button"
                onClick={loadPreview}
                disabled={loading || !selectedRepos.length}
                className="rounded-md bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800 disabled:opacity-60"
              >
                {loading
                  ? "Loading commits…"
                  : "Load commits from selected projects"}
              </button>
              {preview && (
                <button
                  type="button"
                  onClick={() => setEmailOpen(true)}
                  className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-50"
                >
                  Email draft options
                </button>
              )}
            </div>
          </div>
        )}

        {error && (
          <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
            {error}
          </div>
        )}
        </div>
      </div>

      {preview && (
        <div className="mb-6 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="border-b border-slate-100 bg-gradient-to-r from-amber-50 to-white px-6 py-4">
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-amber-800">
              Daily sheet preview
            </p>
            <p className="mt-1 text-sm font-medium text-slate-800">
              {preview.date} ·{" "}
              {preview.used_local_timers
                ? `${preview.session_count || preview.rows.length} local timer${
                    (preview.session_count || preview.rows.length) === 1
                      ? ""
                      : "s"
                  }`
                : `${preview.commit_count} commit${
                    preview.commit_count === 1 ? "" : "s"
                  }`}{" "}
              on {selectedRepos.length} project
              {selectedRepos.length === 1 ? "" : "s"} · Total{" "}
              {preview.total_time}h · {preview.user_name || username}
            </p>
            {preview.used_local_timers && (
              <p className="mt-1 text-xs text-emerald-700">
                In/Out times from saved local branch timers · rows without
                commits are hidden
              </p>
            )}
            <p className="mt-1 text-xs text-slate-500">
              Projects: {selectedRepos.join(", ")}
            </p>
            <p className="text-xs text-slate-500">
              To: {preview.to.join(", ") || "—"}
            </p>
          </div>

          <div className="overflow-x-auto p-4">
            <table className="min-w-full border-collapse overflow-hidden rounded-lg text-sm">
              <thead>
                <tr className="bg-amber-300 text-left text-xs uppercase tracking-wide text-amber-950">
                  <th className="border border-amber-400/60 px-2.5 py-2">#</th>
                  <th className="border border-amber-400/60 px-2.5 py-2">Date</th>
                  <th className="border border-amber-400/60 px-2.5 py-2">In-Time</th>
                  <th className="border border-amber-400/60 px-2.5 py-2">Out-Time</th>
                  <th className="border border-amber-400/60 px-2.5 py-2">
                    Total Time
                  </th>
                  <th className="border border-amber-400/60 px-2.5 py-2">Project</th>
                  <th className="border border-amber-400/60 px-2.5 py-2">Task</th>
                </tr>
              </thead>
              <tbody>
                {previewRows.length === 0 ? (
                  <tr>
                    <td
                      colSpan={7}
                      className="border border-slate-200 px-3 py-6 text-center text-slate-500"
                    >
                      No commits by you on the selected projects for this day.
                    </td>
                  </tr>
                ) : (
                  previewRows.map((r, idx) => (
                    <tr
                      key={`${r.sr}-${r.sha || r.task_summary}`}
                      className="odd:bg-white even:bg-slate-50/70"
                    >
                      <td className="border border-slate-200 px-2.5 py-2 text-center text-slate-600">
                        {idx + 1}
                      </td>
                      <td className="border border-slate-200 px-2.5 py-2 text-center">
                        {r.date}
                      </td>
                      <td className="border border-slate-200 px-2.5 py-2 text-center">
                        {r.in_time}
                      </td>
                      <td className="border border-slate-200 px-2.5 py-2 text-center">
                        {r.out_time}
                      </td>
                      <td className="border border-slate-200 px-2.5 py-2 text-center font-medium text-emerald-800">
                        {r.total_time}
                      </td>
                      <td className="border border-slate-200 px-2.5 py-2 text-center font-medium">
                        {r.project}
                      </td>
                      <td className="border border-slate-200 px-2.5 py-2">
                        {r.task_commits && r.task_commits.length > 0 ? (
                          <div className="space-y-2">
                            {r.task_commits.map((c, i) => (
                              <div key={`${c.short_sha || c.summary}-${i}`}>
                                <div className="font-mono text-[13px] text-slate-800">
                                  {c.summary}
                                </div>
                                {c.url ? (
                                  <a
                                    href={c.url}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="mt-0.5 block break-all text-xs text-sky-700 underline decoration-sky-300 underline-offset-2 hover:text-sky-900"
                                  >
                                    {c.url}
                                  </a>
                                ) : null}
                              </div>
                            ))}
                          </div>
                        ) : (
                          <>
                            <div className="whitespace-pre-line font-mono text-[13px] text-slate-800">
                              {r.task_summary}
                            </div>
                            {r.task_url && (
                              <a
                                href={r.task_url}
                                target="_blank"
                                rel="noreferrer"
                                className="mt-0.5 block break-all text-xs text-sky-700 underline decoration-sky-300 underline-offset-2 hover:text-sky-900"
                              >
                                {r.task_url}
                              </a>
                            )}
                          </>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {emailOpen && preview && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="max-h-[90vh] w-full max-w-3xl overflow-hidden rounded-2xl bg-white shadow-xl">
            <div className="flex items-start justify-between border-b border-slate-200 px-5 py-4">
              <div>
                <h3 className="font-semibold text-slate-900">
                  {preview.subject}
                </h3>
                <p className="text-xs text-slate-600">
                  To: {preview.to.join(", ")} | Total: {preview.total_time}h
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
            <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-200 px-5 py-4">
              <button
                type="button"
                onClick={() => setEmailOpen(false)}
                className="rounded-md border border-slate-300 px-3 py-2 text-sm"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={openMacMail}
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
              <button
                type="button"
                onClick={async () => {
                  await copyTemplate();
                  alert("Template copied.");
                }}
                className="rounded-md border border-slate-300 px-3 py-2 text-sm"
              >
                Copy template
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
