"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import TicketCard from "./TicketCard";
import TicketDetailModal from "./TicketDetailModal";
import { useTicketBoard } from "../hooks/useTicketBoard";
import { formatDuration, useWorkTimer } from "../hooks/useWorkTimer";
import {
  BoardColumn,
  countStoryTasks,
  JiraData,
  JiraWorkflowStatus,
  PRIORITY_COLUMNS,
  SelectedTicket,
  jiraStatusColumnStyle,
  jiraStatusPillStyle
} from "../types";
import {
  getJiraSessionId,
  jiraAuthHeaders,
  setJiraSessionId
} from "../lib/jiraSession";
import EmailRecipientFields from "./EmailRecipientFields";
import WorklogDescriptionModal from "./WorklogDescriptionModal";
import {
  emailListPayload,
  loadEmailRecipients,
} from "../lib/emailRecipients";

import { API } from "../lib/api";

function todayISO() {
  const d = new Date();
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

function shiftISO(iso: string, days: number) {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + days);
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

const columnStyles: Record<BoardColumn, string> = {
  Highest: "border-t-red-500 bg-red-50/40",
  High: "border-t-orange-500 bg-orange-50/40",
  Medium: "border-t-yellow-500 bg-yellow-50/40",
  Low: "border-t-slate-400 bg-slate-50/40",
  Done: "border-t-emerald-500 bg-emerald-50/40"
};

type Project = {
  id: string;
  key: string;
  name: string;
  project_type?: string;
};

type SetupCheck = {
  key: string;
  ok: boolean | null;
  label: string;
  how: string;
};

type SetupInfo = {
  oauth_configured?: boolean;
  redirect_uri?: string;
  missing?: { key: string; label: string; how: string }[];
  checks?: SetupCheck[];
  manual_checks?: SetupCheck[];
  distribution_help?: string;
  console_url?: string;
};

type AuthStatus = {
  connected: boolean;
  oauth_configured?: boolean;
  auth_type?: string;
  user_email?: string;
  user_name?: string;
  site_name?: string;
  site_url?: string;
  cloud_id?: string;
  redirect_uri?: string;
  resources?: { id: string; name?: string; url?: string }[];
  setup?: SetupInfo;
};

type DailyEmailPreview = {
  ok: boolean;
  smtp_ready: boolean;
  date: string;
  subject: string;
  to: string[];
  cc: string[];
  rows: {
    sr: number;
    date: string;
    in_time: string;
    out_time: string;
    total_time: string;
    project: string;
    task: string;
  }[];
  total_time: string;
  html: string;
  text_body: string;
  user_name: string;
};

export default function LiveJiraBoard() {
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectKey, setProjectKey] = useState("");
  const [userName, setUserName] = useState<string | null>(null);
  const [data, setData] = useState<JiraData | null>(null);
  const [loadingAuth, setLoadingAuth] = useState(true);
  const [loadingProjects, setLoadingProjects] = useState(false);
  const [loadingIssues, setLoadingIssues] = useState(false);
  const [lastSyncedAt, setLastSyncedAt] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<SelectedTicket | null>(null);
  const [tokenForm, setTokenForm] = useState({
    base_url: "https://techticsolutions.atlassian.net",
    email: "",
    api_token: ""
  });
  const [connectingToken, setConnectingToken] = useState(false);
  const [selectingSite, setSelectingSite] = useState(false);
  const [emailPreview, setEmailPreview] = useState<DailyEmailPreview | null>(null);
  const [preparingEmail, setPreparingEmail] = useState(false);
  const [sendingEmail, setSendingEmail] = useState(false);
  const [emailDate, setEmailDate] = useState(todayISO());
  const [emailTo, setEmailTo] = useState("");
  const [emailCc, setEmailCc] = useState("");

  const timer = useWorkTimer();
  const { setTotals } = timer;

  useEffect(() => {
    const saved = loadEmailRecipients();
    setEmailTo(saved.to);
    setEmailCc(saved.cc);
  }, []);

  const refreshAuth = useCallback(async () => {
    setLoadingAuth(true);
    try {
      const res = await axios.get<AuthStatus>(`${API}/auth/jira/status`, {
        headers: jiraAuthHeaders()
      });
      setAuth(res.data);
      return res.data;
    } catch {
      setAuth({ connected: false, oauth_configured: false });
      return null;
    } finally {
      setLoadingAuth(false);
    }
  }, []);

  const loadProjects = useCallback(async () => {
    setLoadingProjects(true);
    setError(null);
    try {
      const res = await axios.get(`${API}/jira/projects`, {
        headers: jiraAuthHeaders()
      });
      setProjects(res.data.projects || []);
      const label =
        res.data.user_email || res.data.user_name || res.data.user || null;
      setUserName(label);
      setAuth((prev) =>
        prev
          ? {
              ...prev,
              user_email: res.data.user_email || prev.user_email,
              user_name: res.data.user_name || prev.user_name,
              site_name: res.data.site_name || prev.site_name
            }
          : prev
      );
      if (!projectKey && res.data.projects?.[0]?.key) {
        setProjectKey(res.data.projects[0].key);
      }
    } catch (err: any) {
      setError(
        err?.response?.data?.detail ||
          err?.message ||
          "Could not load projects"
      );
    } finally {
      setLoadingProjects(false);
    }
  }, [projectKey]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const jiraErr = params.get("jira_error");
    if (jiraErr) setError(decodeURIComponent(jiraErr));

    const sid = params.get("sid");
    if (sid) {
      setJiraSessionId(sid);
      params.delete("sid");
      params.delete("jira");
      const next = params.toString();
      window.history.replaceState(
        {},
        "",
        `${window.location.pathname}${next ? `?${next}` : ""}`
      );
    }

    // Pass sid explicitly so status works even before localStorage settles
    const boot = async () => {
      setLoadingAuth(true);
      try {
        let activeSid = sid || getJiraSessionId();
        if (activeSid) {
          const headers: Record<string, string> = {
            "X-Jira-Session": activeSid,
          };
          const res = await axios.get<AuthStatus>(`${API}/auth/jira/status`, {
            headers,
          });
          if (res.data.connected) {
            setJiraSessionId(activeSid);
            setAuth(res.data);
            await loadProjects();
            return;
          }
        }

        // Desktop resume: recover last saved backend session if localStorage is empty/stale
        try {
          const resumeHeaders: Record<string, string> = {};
          if (activeSid) resumeHeaders["X-Jira-Session"] = activeSid;
          const resume = await axios.get(`${API}/auth/jira/resume`, {
            headers: resumeHeaders,
          });
          if (resume.data?.connected && resume.data?.session_id) {
            activeSid = resume.data.session_id;
            setJiraSessionId(activeSid);
            const res = await axios.get<AuthStatus>(`${API}/auth/jira/status`, {
              headers: { "X-Jira-Session": activeSid },
            });
            setAuth(res.data);
            if (res.data.connected) await loadProjects();
            return;
          }
        } catch {
          // Resume endpoint is desktop-only; ignore on deployed web.
        }

        const res = await axios.get<AuthStatus>(`${API}/auth/jira/status`, {
          headers: activeSid ? { "X-Jira-Session": activeSid } : {},
        });
        setAuth(res.data);
        if (res.data.connected) await loadProjects();
      } catch {
        setAuth({ connected: false, oauth_configured: false });
      } finally {
        setLoadingAuth(false);
      }
    };
    void boot();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fetchProjectIssues = useCallback(async (key?: string) => {
    const pk = key || projectKey;
    if (!pk) {
      setError("Select a project first");
      return;
    }
    setLoadingIssues(true);
    setError(null);
    try {
      const sprintRes = await axios.get<JiraData>(
        `${API}/jira/project/${encodeURIComponent(pk)}/issues`,
        {
          params: { max_results: 1000, current_sprint: true },
          headers: jiraAuthHeaders()
        }
      );
      const sprintCount =
        sprintRes.data.fetched ??
        sprintRes.data.modules?.reduce((n, m) => n + (m.stories?.length || 0), 0) ??
        0;

      let chosenData = sprintRes.data;
      if (sprintCount === 0) {
        const allRes = await axios.get<JiraData>(
          `${API}/jira/project/${encodeURIComponent(pk)}/issues`,
          {
            params: { max_results: 1000, current_sprint: false },
            headers: jiraAuthHeaders()
          }
        );
        chosenData = allRes.data;
      }

      setData(chosenData);
      setLastSyncedAt(new Date());
      if (chosenData.logged_seconds) {
        setTotals(chosenData.logged_seconds);
      }
    } catch (err: any) {
      setData(null);
      setError(
        err?.response?.data?.detail ||
          err?.message ||
          "Failed to fetch project tickets"
      );
    } finally {
      setLoadingIssues(false);
    }
  }, [projectKey, setTotals]);

  const logout = async () => {
    await axios.post(
      `${API}/auth/jira/logout`,
      {},
      { headers: jiraAuthHeaders() }
    );
    setJiraSessionId(null);
    setAuth({ connected: false, oauth_configured: true, setup: auth?.setup });
    setProjects([]);
    setData(null);
    setUserName(null);
  };

  const connectWithApiToken = async () => {
    setConnectingToken(true);
    setError(null);
    try {
      const res = await axios.post(`${API}/auth/jira/api-token`, tokenForm, {
        headers: jiraAuthHeaders()
      });
      if (res.data.session_id) {
        setJiraSessionId(res.data.session_id);
      }
      await refreshAuth();
      await loadProjects();
    } catch (err: any) {
      setError(
        err?.response?.data?.detail ||
          err?.message ||
          "API token login failed"
      );
    } finally {
      setConnectingToken(false);
    }
  };

  const selectSite = async (cloudId: string) => {
    setSelectingSite(true);
    setError(null);
    try {
      await axios.post(
        `${API}/auth/jira/site`,
        { cloud_id: cloudId },
        { headers: jiraAuthHeaders() }
      );
      await refreshAuth();
      setProjects([]);
      setData(null);
      await loadProjects();
    } catch (err: any) {
      setError(
        err?.response?.data?.detail || err?.message || "Could not switch site"
      );
    } finally {
      setSelectingSite(false);
    }
  };

  const [todayLabel, setTodayLabel] = useState("");
  useEffect(() => {
    setTodayLabel(
      new Date().toLocaleDateString("en-US", {
        weekday: "long",
        month: "short",
        day: "numeric",
        year: "numeric",
      })
    );
  }, []);

  const previewDailyEmail = async () => {
    setPreparingEmail(true);
    setError(null);
    try {
      const res = await axios.post<DailyEmailPreview>(
        `${API}/reports/daily-email/preview`,
        {
          date: emailDate,
          ...emailListPayload(emailTo, emailCc),
        },
        { headers: jiraAuthHeaders() }
      );
      setEmailPreview(res.data);
      if (!emailTo.trim() && res.data.to?.length) {
        setEmailTo(res.data.to.join(", "));
      }
      if (!emailCc.trim() && res.data.cc?.length) {
        setEmailCc(res.data.cc.join(", "));
      }
    } catch (err: any) {
      setError(
        err?.response?.data?.detail ||
          err?.message ||
          "Could not prepare daily email preview"
      );
    } finally {
      setPreparingEmail(false);
    }
  };

  const sendDailyEmail = async () => {
    setSendingEmail(true);
    setError(null);
    try {
      const res = await axios.post(
        `${API}/reports/daily-email/send`,
        {
          date: emailDate,
          ...emailListPayload(emailTo, emailCc),
        },
        { headers: jiraAuthHeaders() }
      );
      if (res.data?.sent) {
        setEmailPreview(null);
      }
      alert("Email sent successfully.");
    } catch (err: any) {
      setError(
        err?.response?.data?.detail || err?.message || "Could not send email"
      );
    } finally {
      setSendingEmail(false);
    }
  };

  const openMailtoDraft = () => {
    if (!emailPreview) return;
    const toList = (
      emailTo.trim()
        ? emailTo.split(",")
        : emailPreview.to || []
    )
      .map((x) => x.trim())
      .filter(Boolean);
    const ccList = (
      emailCc.trim()
        ? emailCc.split(",")
        : emailPreview.cc || []
    )
      .map((x) => x.trim())
      .filter(Boolean);

    const toPath = toList.join(",");

    // Use explicit encoding for query values only.
    // Do not encode the full "to" path; over-encoding can break Mail.app.
    const query: string[] = [];
    if (ccList.length) {
      query.push(`cc=${encodeURIComponent(ccList.join(","))}`);
    }
    query.push(`subject=${encodeURIComponent(emailPreview.subject || "")}`);

    const body =
      (emailPreview.text_body || "").length > 6000
        ? `${(emailPreview.text_body || "").slice(0, 6000)}\n\n[truncated in mailto draft]`
        : emailPreview.text_body || "";
    query.push(`body=${encodeURIComponent(body)}`);

    const mailto = `mailto:${toPath}${query.length ? `?${query.join("&")}` : ""}`;

    // Anchor click is more reliable than assigning location in some browsers on macOS.
    const a = document.createElement("a");
    a.href = mailto;
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  const copyEmailTemplate = async () => {
    if (!emailPreview) return false;
    const html = emailPreview.html || "";
    const text = emailPreview.text_body || "";
    try {
      if (navigator.clipboard && "write" in navigator.clipboard && html) {
        const item = new ClipboardItem({
          "text/html": new Blob([html], { type: "text/html" }),
          "text/plain": new Blob([text], { type: "text/plain" }),
        });
        await navigator.clipboard.write([item]);
        return true;
      }
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch {
      // Clipboard can fail on some browsers without user gesture / permissions.
    }
    return false;
  };

  const isLocalBackend =
    API.includes("localhost") || API.includes("127.0.0.1");

  const openMacMailDraft = async () => {
    if (!emailPreview) return;
    setError(null);
    try {
      const copied = await copyEmailTemplate();

      // Deployed backend (Railway) cannot open Apple Mail — use mailto on the user's Mac.
      if (!isLocalBackend) {
        openMailtoDraft();
        alert(
          copied
            ? "Mail app should open with To/Subject. Press Cmd+V in the body to paste the formatted sheet."
            : "Mail app should open with To/Subject. Copy the template first if the body is empty."
        );
        return;
      }

      await axios.post(
        `${API}/reports/daily-email/open-mail-app`,
        {
          date: emailDate,
          ...emailListPayload(emailTo, emailCc),
        },
        { headers: jiraAuthHeaders(), timeout: 30000 }
      );
    } catch (err: any) {
      // Fall back to mailto so Mac Mail still opens even if the local draft write fails.
      try {
        const copied = await copyEmailTemplate();
        openMailtoDraft();
        alert(
          copied
            ? "Opened Mac Mail via fallback. Press Cmd+V in the body to paste the sheet."
            : "Opened Mac Mail via fallback. Copy the template if the body is empty."
        );
        setError(null);
      } catch {
        setError(
          err?.response?.data?.detail ||
            err?.message ||
            "Could not open Apple Mail draft"
        );
      }
    }
  };

  const openGmailDraft = async () => {
    if (!emailPreview) return;
    setError(null);
    try {
      const copied = await copyEmailTemplate();

      const params = new URLSearchParams();
      params.set("view", "cm");
      params.set("fs", "1");
      params.set("tf", "1");
      if (emailTo.trim() || emailPreview.to.length)
        params.set(
          "to",
          emailTo.trim() || emailPreview.to.join(",")
        );
      if (emailCc.trim() || emailPreview.cc.length)
        params.set(
          "cc",
          emailCc.trim() || emailPreview.cc.join(",")
        );
      params.set("su", emailPreview.subject);
      window.open(
        `https://mail.google.com/mail/?${params.toString()}`,
        "_blank",
        "noopener,noreferrer"
      );
      alert(
        copied
          ? "Gmail draft opened with To/Subject. Press Cmd+V in the body to paste the template."
          : "Gmail opened. Use Copy template, then Cmd+V in the body."
      );
    } catch (err: any) {
      setError(err?.message || "Could not open Gmail draft");
    }
  };

  const copyTemplateHtml = async () => {
    if (!emailPreview) return;
    try {
      const html = emailPreview.html || "";
      const text = emailPreview.text_body || "";

      // Rich copy: preserves table layout/styles when pasted in mail clients.
      if (navigator.clipboard && "write" in navigator.clipboard && html) {
        const item = new ClipboardItem({
          "text/html": new Blob([html], { type: "text/html" }),
          "text/plain": new Blob([text], { type: "text/plain" })
        });
        await navigator.clipboard.write([item]);
      } else if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        throw new Error("Clipboard API not supported");
      }
      alert("Template copied. Paste it into your email compose window.");
    } catch (err: any) {
      setError(
        err?.message ||
          "Could not copy formatted template. Try Copy text as fallback."
      );
    }
  };

  return (
    <div className="mt-2">
      <div className="mb-6 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-sm font-semibold uppercase tracking-wide text-teal-700">
              Live Jira · {todayLabel || "Today"}
            </p>
            <h2 className="text-2xl font-bold text-slate-900">
              Project board + work timer
            </h2>
            <p className="mt-1 max-w-2xl text-sm text-slate-600">
              <strong>Purpose:</strong> log real time against Jira tickets while
              you work, then export a Today Task sheet for TL/PM/HR.
            </p>
            <p className="mt-1 max-w-2xl text-sm text-slate-600">
              Connect Atlassian, pick a project, load the{" "}
              <strong>current sprint</strong>, Start/Stop timers, then Preview
              daily email.
            </p>
          </div>
          {auth?.connected && (
            <div className="flex flex-col items-start gap-1 sm:items-end">
              <span className="rounded-full bg-teal-50 px-3 py-1 text-sm font-medium text-teal-800">
                {auth.user_email || auth.user_name || userName
                  ? `Signed in as ${auth.user_email || auth.user_name || userName}`
                  : "Connected — refresh projects to load your profile"}
              </span>
              {auth.site_name && (
                <span className="text-xs text-slate-500">
                  Site: {auth.site_name}
                </span>
              )}
            </div>
          )}        </div>

        {loadingAuth ? (
          <p className="text-sm text-slate-500">Checking Jira connection…</p>
        ) : !auth?.connected ? (
          <div className="space-y-4">
            {(auth?.setup?.missing?.length || !auth?.oauth_configured) && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950">
                <p className="font-semibold">Missing config — add these, then restart the backend</p>
                <ul className="mt-2 list-disc space-y-1 pl-5">
                  {(auth?.setup?.missing || []).map((m) => (
                    <li key={m.key}>
                      <code className="rounded bg-amber-100/80 px-1">{m.label}</code>
                      <span className="text-amber-900/80"> — {m.how}</span>
                    </li>
                  ))}
                  {!auth?.setup?.missing?.length && !auth?.oauth_configured && (
                    <li>
                      Set <code>JIRA_CLIENT_ID</code> and{" "}
                      <code>JIRA_CLIENT_SECRET</code> in{" "}
                      <code>backend/.env</code>
                    </li>
                  )}
                </ul>
              </div>
            )}

            <div className="rounded-lg border border-teal-200 bg-teal-50/60 px-4 py-3 text-sm text-teal-950">
              <p className="font-semibold">
                Any Jira user can connect — do not add Collaborators
              </p>
              <p className="mt-1 text-teal-900/90">
                {auth?.setup?.distribution_help ||
                  'In Atlassian Developer Console → your app → Distribution → enable "Sharing". Each person clicks Connect and authorizes their own account.'}
              </p>
              <a
                href={
                  auth?.setup?.console_url ||
                  "https://developer.atlassian.com/console/myapps/"
                }
                target="_blank"
                rel="noreferrer"
                className="mt-2 inline-block text-sm font-medium text-teal-800 underline"
              >
                Open Developer Console
              </a>
            </div>

            <a
              href={`${API}/auth/jira/login`}
              className="inline-flex rounded-lg bg-teal-700 px-4 py-2.5 text-sm font-semibold text-white hover:bg-teal-800"
            >
              Connect with Jira (OAuth)
            </a>
            <p className="text-xs text-slate-500">
              Each person must use their own Atlassian{" "}
              <strong>work</strong> account that can open{" "}
              <code className="rounded bg-slate-100 px-1">
                techticsolutions.atlassian.net
              </code>
              . Personal Gmail Atlassian logins will fail. Admin: enable{" "}
              <strong>Distribution → Sharing</strong> on the OAuth app. Callback:{" "}
              <code className="rounded bg-slate-100 px-1">
                {auth?.redirect_uri ||
                  auth?.setup?.redirect_uri ||
                  `${API}/callback`}
              </code>
            </p>

            <div className="relative py-2">
              <div className="absolute inset-0 flex items-center">
                <div className="w-full border-t border-slate-200" />
              </div>
              <div className="relative flex justify-center">
                <span className="bg-white px-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Or skip OAuth — API token
                </span>
              </div>
            </div>

            <div className="rounded-lg border border-slate-200 bg-slate-50/80 p-4 space-y-3">
              <p className="text-sm text-slate-700">
                Create a token at{" "}
                <a
                  href="https://id.atlassian.com/manage-profile/security/api-tokens"
                  target="_blank"
                  rel="noreferrer"
                  className="font-medium text-teal-800 underline"
                >
                  id.atlassian.com → API tokens
                </a>
                , then paste it below.
              </p>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="block text-sm sm:col-span-2">
                  <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Jira site URL
                  </span>
                  <input
                    value={tokenForm.base_url}
                    onChange={(e) =>
                      setTokenForm((f) => ({ ...f, base_url: e.target.value }))
                    }
                    className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100"
                    placeholder="https://your-domain.atlassian.net"
                  />
                </label>
                <label className="block text-sm">
                  <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Email
                  </span>
                  <input
                    value={tokenForm.email}
                    onChange={(e) =>
                      setTokenForm((f) => ({ ...f, email: e.target.value }))
                    }
                    className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100"
                    placeholder="you@company.com"
                  />
                </label>
                <label className="block text-sm">
                  <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                    API token
                  </span>
                  <input
                    type="password"
                    value={tokenForm.api_token}
                    onChange={(e) =>
                      setTokenForm((f) => ({ ...f, api_token: e.target.value }))
                    }
                    className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100"
                    placeholder="Paste API token"
                  />
                </label>
              </div>
              <button
                type="button"
                onClick={connectWithApiToken}
                disabled={
                  connectingToken ||
                  !tokenForm.base_url.trim() ||
                  !tokenForm.email.trim() ||
                  !tokenForm.api_token.trim()
                }
                className="rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white hover:bg-slate-800 disabled:opacity-60"
              >
                {connectingToken ? "Connecting…" : "Connect with API token"}
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-end gap-3">
              {(auth.resources?.length || 0) > 1 && (
                <div className="min-w-[200px]">
                  <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Jira site
                  </label>
                  <select
                    value={auth.cloud_id || ""}
                    disabled={selectingSite}
                    onChange={(e) => {
                      if (e.target.value) void selectSite(e.target.value);
                    }}
                    className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100"
                  >
                    {(auth.resources || []).map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.name || r.url || r.id}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <div className="min-w-[220px] flex-1">
                <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Your projects
                </label>
                <select
                  value={projectKey}
                  onChange={(e) => setProjectKey(e.target.value)}
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100"
                >
                  <option value="">Select a project you work on…</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.key}>
                      {p.name} ({p.key})
                    </option>
                  ))}
                </select>
              </div>
              <button
                type="button"
                onClick={() => loadProjects()}
                disabled={loadingProjects}
                className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-50 disabled:opacity-60"
              >
                {loadingProjects ? "Refreshing…" : "Refresh projects"}
              </button>
              <button
                type="button"
                onClick={() => fetchProjectIssues()}
                disabled={loadingIssues || !projectKey}
                className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-800 disabled:opacity-60"
              >
                {loadingIssues
                  ? "Refreshing…"
                  : data
                    ? "↻ Refresh sprint"
                    : "Load current sprint"}
              </button>
              <button
                type="button"
                onClick={logout}
                className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50"
              >
                Disconnect
              </button>
            </div>

            <div className="rounded-xl border border-indigo-100 bg-indigo-50/40 p-4">
              <div className="mb-3 flex flex-wrap items-end gap-3">
                <label className="text-sm text-slate-700">
                  Sheet date
                  <input
                    type="date"
                    value={emailDate}
                    onChange={(e) => setEmailDate(e.target.value)}
                    className="mt-1 block rounded-md border border-slate-300 px-3 py-2 text-sm"
                  />
                </label>
                <button
                  type="button"
                  onClick={() => setEmailDate(todayISO())}
                  className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-50"
                >
                  Today
                </button>
                <button
                  type="button"
                  onClick={() => setEmailDate(shiftISO(todayISO(), -1))}
                  className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-50"
                >
                  Yesterday
                </button>
                <button
                  type="button"
                  onClick={previewDailyEmail}
                  disabled={preparingEmail}
                  className="rounded-lg border border-indigo-300 bg-indigo-50 px-4 py-2 text-sm font-semibold text-indigo-800 hover:bg-indigo-100 disabled:opacity-60"
                >
                  {preparingEmail ? "Preparing email…" : "Preview daily email"}
                </button>
              </div>
              <EmailRecipientFields
                to={emailTo}
                cc={emailCc}
                onChange={({ to, cc }) => {
                  setEmailTo(to);
                  setEmailCc(cc);
                }}
              />
            </div>

            {lastSyncedAt && data && (
              <p className="text-xs text-slate-500">
                Loaded{" "}
                {data.fetched ??
                  data.modules?.reduce(
                    (n, m) => n + (m.stories?.length || 0),
                    0
                  ) ??
                  0}{" "}
                {data.mode === "project" ? "project tickets" : "current-sprint tickets"} · last synced{" "}
                {lastSyncedAt.toLocaleTimeString(undefined, {
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit"
                })}
                {data.may_have_more
                  ? " · hit the 1000-ticket cap"
                  : ""}{" "}
                — click Refresh sprint to pull latest statuses from Jira.
              </p>
            )}

            {timer.active && (
              <div className="flex flex-wrap items-center gap-3 rounded-xl border border-teal-200 bg-teal-50 px-4 py-3">
                <span className="relative flex h-2.5 w-2.5">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-teal-400 opacity-75" />
                  <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-teal-600" />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-teal-900">
                    Timer · {timer.active.issueKey}
                  </p>
                  <p className="truncate text-xs text-teal-800">
                    {timer.active.title}
                  </p>
                </div>
                <p className="font-mono text-xl font-bold tabular-nums text-teal-900">
                  {formatDuration(timer.elapsed)}
                </p>
                <button
                  type="button"
                  disabled={timer.logging || !!timer.pending}
                  onClick={() => timer.requestStop()}
                  className="rounded-lg bg-teal-800 px-3 py-1.5 text-sm font-semibold text-white hover:bg-teal-900 disabled:opacity-60"
                >
                  {timer.pending
                    ? "Awaiting description…"
                    : timer.logging
                      ? "Logging…"
                      : "Stop & log"}
                </button>
                <button
                  type="button"
                  onClick={timer.discard}
                  disabled={timer.logging}
                  className="rounded-lg px-3 py-1.5 text-sm text-teal-800 hover:bg-teal-100 disabled:opacity-60"
                >
                  Discard
                </button>
              </div>
            )}

            {timer.lastMessage && !timer.pending && (
              <p className="text-sm text-teal-800">{timer.lastMessage}</p>
            )}
          </div>
        )}

        {error && (
          <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
            {error}
          </div>
        )}
      </div>

      {loadingIssues && (
        <div className="mb-8 flex items-center gap-3 rounded-xl border border-teal-200 bg-teal-50 p-5">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-teal-700 border-t-transparent" />
          <p className="font-medium text-teal-900">
            Fetching current sprint tickets from Jira…
          </p>
        </div>
      )}

      {data && !data.error && (data.modules?.length ?? 0) > 0 && (
        <SyncedBoard
          data={data}
          selected={selected}
          setSelected={setSelected}
          timer={timer}
          onRefresh={() => fetchProjectIssues()}
          refreshing={loadingIssues}
          lastSyncedAt={lastSyncedAt}
        />
      )}

      {data && !data.error && (!data.modules || data.modules.length === 0) && (
        <div className="rounded-xl border border-amber-300 bg-amber-50 p-4 text-amber-900">
          No issues found in this project.
        </div>
      )}

      {emailPreview && (
        <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-slate-900/60 p-4">
          <div className="my-auto flex max-h-[min(92vh,920px)] w-full max-w-5xl flex-col overflow-hidden rounded-xl bg-white shadow-2xl">
            <div className="flex shrink-0 items-start justify-between gap-3 border-b border-slate-200 px-5 py-4">
              <div>
                <p className="text-xs uppercase tracking-wide text-slate-500">
                  Daily Email Preview
                </p>
                <h3 className="text-lg font-bold text-slate-900">
                  {emailPreview.subject}
                </h3>
                <p className="mt-1 text-xs text-slate-600">
                  Date: {emailPreview.date} | Total: {emailPreview.total_time}h
                </p>
              </div>
              <button
                type="button"
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
                onClick={() => setEmailPreview(null)}
              >
                Close
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              <div className="border-b border-slate-200 px-5 py-4">
                <EmailRecipientFields
                  to={emailTo}
                  cc={emailCc}
                  onChange={({ to, cc }) => {
                    setEmailTo(to);
                    setEmailCc(cc);
                  }}
                />
                <div className="mt-3 flex flex-wrap items-end gap-3">
                  <label className="text-sm text-slate-700">
                    Sheet date
                    <input
                      type="date"
                      value={emailDate}
                      onChange={(e) => setEmailDate(e.target.value)}
                      className="mt-1 block rounded-md border border-slate-300 px-3 py-2 text-sm"
                    />
                  </label>
                  <button
                    type="button"
                    onClick={() => setEmailDate(todayISO())}
                    className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-50"
                  >
                    Today
                  </button>
                  <button
                    type="button"
                    onClick={() => setEmailDate(shiftISO(todayISO(), -1))}
                    className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-50"
                  >
                    Yesterday
                  </button>
                  <button
                    type="button"
                    onClick={previewDailyEmail}
                    disabled={preparingEmail}
                    className="rounded-md border border-indigo-300 bg-indigo-50 px-3 py-2 text-sm font-semibold text-indigo-800 hover:bg-indigo-100 disabled:opacity-60"
                  >
                    {preparingEmail ? "Refreshing…" : "Refresh preview"}
                  </button>
                </div>
              </div>
              <div className="p-5">
                <div
                  className="prose max-w-none"
                  dangerouslySetInnerHTML={{ __html: emailPreview.html }}
                />
              </div>
            </div>
            <div className="flex shrink-0 flex-wrap items-center justify-end gap-3 border-t border-slate-200 bg-white px-5 py-4">
              <span className="mr-auto max-w-sm text-xs text-slate-600">
                Prefer Gmail on the deployed site. Mac Mail works fully only with a
                local Mac backend — otherwise it opens mailto + paste (Cmd+V).
              </span>
              <button
                type="button"
                onClick={() => setEmailPreview(null)}
                className="rounded-md border border-slate-300 px-4 py-2 text-sm text-slate-700 hover:bg-slate-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={openMacMailDraft}
                className="rounded-md bg-emerald-700 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-800"
              >
                Open in Mac Mail
              </button>
              <button
                type="button"
                onClick={openGmailDraft}
                className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
              >
                Open Gmail draft
              </button>
              {emailPreview.smtp_ready && (
                <button
                  type="button"
                  onClick={sendDailyEmail}
                  disabled={sendingEmail}
                  className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-60"
                >
                  {sendingEmail ? "Sending…" : "Send via SMTP (optional)"}
                </button>
              )}
              <button
                type="button"
                onClick={copyTemplateHtml}
                className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm text-slate-700 hover:bg-slate-50"
              >
                Copy formatted template
              </button>
              <button
                type="button"
                onClick={() => {
                  navigator.clipboard.writeText(emailPreview.text_body);
                }}
                className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm text-slate-700 hover:bg-slate-50"
              >
                Copy text
              </button>
            </div>
          </div>
        </div>
      )}

      {timer.pending && (
        <WorklogDescriptionModal
          pending={timer.pending}
          logging={timer.logging}
          error={timer.lastMessage}
          onCancel={timer.cancelPending}
          onSave={timer.confirmLog}
        />
      )}
    </div>
  );
}

function SyncedBoard({
  data,
  selected,
  setSelected,
  timer,
  onRefresh,
  refreshing,
  lastSyncedAt
}: {
  data: JiraData;
  selected: SelectedTicket | null;
  setSelected: (t: SelectedTicket | null) => void;
  timer: ReturnType<typeof useWorkTimer>;
  onRefresh: () => void;
  refreshing: boolean;
  lastSyncedAt: Date | null;
}) {
  const {
    tickets,
    ticketsByColumn,
    ticketsByModule,
    ticketsByJiraStatus,
    jiraStatusColumns,
    view,
    setView,
    draggingId,
    setDraggingId,
    moveTicket,
    updateJiraStatus,
    toggleLayerTask,
    toggleCriterion,
    markComplete,
    completedCount,
    workingNowCount
  } = useTicketBoard(data);

  const [statusError, setStatusError] = useState<string | null>(null);
  const [syncingId, setSyncingId] = useState<string | null>(null);

  const selectedLive = useMemo(() => {
    if (!selected) return null;
    return tickets.find((t) => t.story.id === selected.story.id) || selected;
  }, [selected, tickets]);

  const stats = useMemo(
    () => ({
      stories: tickets.length,
      completed: completedCount,
      working: workingNowCount,
      tasks: tickets.reduce((n, t) => n + countStoryTasks(t.story), 0),
      modules: data.modules?.length ?? 0
    }),
    [tickets, completedCount, workingNowCount, data.modules]
  );

  const handleDrop = (column: BoardColumn, e: React.DragEvent) => {
    e.preventDefault();
    const id = e.dataTransfer.getData("text/plain");
    if (id) moveTicket(id, column);
    setDraggingId(null);
  };

  const handleStatusDrop = async (
    status: JiraWorkflowStatus,
    e: React.DragEvent
  ) => {
    e.preventDefault();
    const id = e.dataTransfer.getData("text/plain");
    setDraggingId(null);
    if (!id) return;

    const ticket = tickets.find((t) => t.story.id === id);
    if (!ticket) return;

    const currentName = ticket.story.jira_meta?.status_name;
    if (currentName === status.name) return;

    const previousStatus = currentName;

    updateJiraStatus(id, status.name, status.category);
    setStatusError(null);
    setSyncingId(id);

    try {
      const res = await axios.post(
        `${API}/jira/issues/${encodeURIComponent(id)}/transition`,
        { target_status: status.name },
        { headers: jiraAuthHeaders() }
      );
      if (res.data?.status_name) {
        updateJiraStatus(id, res.data.status_name);
      }
    } catch (err: any) {
      if (previousStatus) updateJiraStatus(id, previousStatus);
      setStatusError(
        err?.response?.data?.detail ||
          err?.message ||
          `Could not move ${id} to ${status.name} in Jira`
      );
    } finally {
      setSyncingId(null);
    }
  };

  const renderTicketCard = (ticket: (typeof tickets)[number]) => (
    <TicketCard
      key={ticket.story.id}
      ticket={ticket}
      onOpen={() => setSelected(ticket)}
      onDragStart={() => setDraggingId(ticket.story.id)}
      onDragEnd={() => setDraggingId(null)}
      onMarkComplete={() => markComplete(ticket.story.id)}
      loggedSeconds={timer.totals[ticket.story.id] || 0}
      timerActive={timer.active?.issueKey === ticket.story.id}
      timerElapsed={
        timer.active?.issueKey === ticket.story.id ? timer.elapsed : 0
      }
      onStartTimer={() => {
        // Timer only — do not auto-change Jira status / column.
        timer.start(ticket.story.id, ticket.story.title);
      }}
      onStopTimer={() => timer.requestStop()}
      timerBusy={timer.logging || syncingId === ticket.story.id}
    />
  );

  return (
    <>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-semibold uppercase tracking-wide text-teal-700">
            Synced from Jira
          </p>
          <h3 className="text-3xl font-bold text-slate-900">{data.project_name}</h3>
          {lastSyncedAt && (
            <p className="mt-1 text-xs text-slate-500">
              Statuses as of{" "}
              {lastSyncedAt.toLocaleTimeString(undefined, {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit"
              })}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshing}
            title="Fetch latest ticket statuses from Jira"
            className="rounded-lg border border-teal-300 bg-white px-4 py-2 text-sm font-semibold text-teal-800 hover:bg-teal-50 disabled:opacity-60"
          >
            {refreshing ? (
              <span className="inline-flex items-center gap-2">
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-teal-700 border-t-transparent" />
                Syncing…
              </span>
            ) : (
              "↻ Refresh from Jira"
            )}
          </button>
          <StatPill label="Tickets" value={stats.stories} />
          <StatPill label="Working" value={stats.working} />
          <StatPill label="Done" value={stats.completed} />
          <StatPill label="Checklists" value={stats.tasks} />
        </div>
      </div>

      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-slate-500">
          {view === "jira_status"
            ? "Columns match your Jira board workflow (QA In Progress, Dev Completed, Ready for QA, etc.). Drag tickets to change status — updates sync to Jira."
            : "Start a timer on a ticket while you work. Stop & log asks for a description, then writes that comment to the Jira issue worklog."}
        </p>
        <div className="flex flex-wrap rounded-lg border border-slate-200 bg-white p-1">
          <ViewBtn
            active={view === "priority"}
            onClick={() => setView("priority")}
          >
            By priority
          </ViewBtn>
          <ViewBtn active={view === "module"} onClick={() => setView("module")}>
            By module
          </ViewBtn>
          <ViewBtn
            active={view === "jira_status"}
            onClick={() => setView("jira_status")}
          >
            By Jira statuses
          </ViewBtn>
        </div>
      </div>

      {statusError && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {statusError}
        </div>
      )}

      {view === "priority" ? (
        <div className="flex gap-4 overflow-x-auto pb-4">
          {PRIORITY_COLUMNS.map((column) => (
            <div
              key={column}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => handleDrop(column, e)}
              className={`flex w-80 shrink-0 flex-col rounded-xl border-t-4 p-3 ${columnStyles[column]} ${
                draggingId ? "ring-2 ring-teal-200 ring-offset-2" : ""
              }`}
            >
              <div className="mb-3 flex items-center justify-between px-1">
                <h3 className="font-bold text-slate-800">{column}</h3>
                <span className="rounded-full bg-white/80 px-2 py-0.5 text-xs font-semibold text-slate-700">
                  {ticketsByColumn[column].length}
                </span>
              </div>
              <div className="max-h-[70vh] flex-1 space-y-0 overflow-y-auto pr-1">
                {ticketsByColumn[column].map(renderTicketCard)}
              </div>
            </div>
          ))}
        </div>
      ) : view === "module" ? (
        <div className="flex gap-4 overflow-x-auto pb-4">
          {[...ticketsByModule.entries()].map(([moduleName, moduleTickets]) => (
            <div
              key={moduleName}
              className="flex w-80 shrink-0 flex-col rounded-xl bg-slate-200/80 p-3"
            >
              <div className="mb-3 flex items-center justify-between px-1">
                <h3 className="font-bold text-slate-800">{moduleName}</h3>
                <span className="rounded-full bg-slate-300/80 px-2 py-0.5 text-xs font-semibold text-slate-700">
                  {moduleTickets.length}
                </span>
              </div>
              <div className="max-h-[70vh] flex-1 space-y-0 overflow-y-auto pr-1">
                {moduleTickets.map(renderTicketCard)}
              </div>
            </div>
          ))}
        </div>
      ) : jiraStatusColumns.length === 0 ? (
        <p className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-6 text-sm text-slate-600">
          No workflow statuses loaded yet. Click{" "}
          <strong>Load tickets</strong> or{" "}
          <strong>Refresh from Jira</strong> to fetch your board statuses.
        </p>
      ) : (
        <div className="flex gap-4 overflow-x-auto pb-4">
          {jiraStatusColumns.map((status) => (
            <div
              key={status.id || status.name}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => handleStatusDrop(status, e)}
              className={`flex w-72 shrink-0 flex-col rounded-xl border-t-4 p-3 ${jiraStatusColumnStyle(status.category)} ${
                draggingId ? "ring-2 ring-teal-200 ring-offset-2" : ""
              }`}
            >
              <div className="mb-3 flex items-start justify-between gap-2 px-1">
                <div className="min-w-0">
                  <span
                    className={`inline-block max-w-full truncate rounded px-2 py-0.5 text-xs font-semibold ${jiraStatusPillStyle(status.category)}`}
                    title={status.name}
                  >
                    {status.name}
                  </span>
                  {status.column_name &&
                    status.column_name !== status.name && (
                      <p className="mt-1 truncate text-[10px] font-medium uppercase tracking-wide text-slate-500">
                        {status.column_name}
                      </p>
                    )}
                </div>
                <span className="shrink-0 rounded-full bg-white/80 px-2 py-0.5 text-xs font-semibold text-slate-700">
                  {(ticketsByJiraStatus[status.name] || []).length}
                </span>
              </div>
              <div className="max-h-[70vh] flex-1 space-y-0 overflow-y-auto pr-1">
                {(ticketsByJiraStatus[status.name] || []).map(renderTicketCard)}
              </div>
            </div>
          ))}
        </div>
      )}

      <TicketDetailModal
        ticket={selectedLive}
        onClose={() => setSelected(null)}
        onToggleLayerTask={(layer, i) =>
          selectedLive && toggleLayerTask(selectedLive.story.id, layer, i)
        }
        onToggleCriterion={(i) =>
          selectedLive && toggleCriterion(selectedLive.story.id, i)
        }
        onMarkComplete={() =>
          selectedLive && markComplete(selectedLive.story.id)
        }
      />
    </>
  );
}

function StatPill({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-center shadow-sm">
      <p className="text-2xl font-bold text-slate-900">{value}</p>
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </p>
    </div>
  );
}

function ViewBtn({
  active,
  onClick,
  children
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-md px-3 py-1.5 text-sm font-medium ${
        active ? "bg-teal-700 text-white" : "text-slate-600 hover:bg-slate-100"
      }`}
    >
      {children}
    </button>
  );
}
