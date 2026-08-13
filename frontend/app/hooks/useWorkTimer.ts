"use client";

import { useCallback, useEffect, useState } from "react";
import axios from "axios";
import { jiraAuthHeaders } from "../lib/jiraSession";

import { API } from "../lib/api";
const TIMER_KEY = "jira-active-timer-v1";

export type ActiveTimer = {
  issueKey: string;
  title: string;
  startedAt: string; // ISO
};

export type PendingWorklog = ActiveTimer & {
  endedAt: string;
  seconds: number;
};

export type WorklogResult = {
  ok: boolean;
  local?: { id: string; seconds: number; pushed_to_jira?: boolean };
  totals?: Record<string, number>;
};

function loadTimer(): ActiveTimer | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(TIMER_KEY);
    return raw ? (JSON.parse(raw) as ActiveTimer) : null;
  } catch {
    return null;
  }
}

function saveTimer(timer: ActiveTimer | null) {
  if (!timer) localStorage.removeItem(TIMER_KEY);
  else localStorage.setItem(TIMER_KEY, JSON.stringify(timer));
}

export function formatDuration(totalSeconds: number) {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) {
    return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  }
  return `${m}:${String(sec).padStart(2, "0")}`;
}

export function useWorkTimer() {
  const [active, setActive] = useState<ActiveTimer | null>(null);
  const [pending, setPending] = useState<PendingWorklog | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [logging, setLogging] = useState(false);
  const [totals, setTotals] = useState<Record<string, number>>({});
  const [lastMessage, setLastMessage] = useState<string | null>(null);

  useEffect(() => {
    setActive(loadTimer());
    axios
      .get(`${API}/jira/worklogs`)
      .then((res) => setTotals(res.data.totals || {}))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!active || pending) {
      if (pending) setElapsed(pending.seconds);
      else if (!active) setElapsed(0);
      return;
    }
    const tick = () => {
      const start = new Date(active.startedAt).getTime();
      setElapsed(Math.floor((Date.now() - start) / 1000));
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [active, pending]);

  const start = useCallback((issueKey: string, title: string) => {
    if (pending) return;
    const timer: ActiveTimer = {
      issueKey,
      title,
      startedAt: new Date().toISOString()
    };
    saveTimer(timer);
    setActive(timer);
    setPending(null);
    setLastMessage(null);
  }, [pending]);

  /** Freeze the timer and ask for a worklog description before posting to Jira. */
  const requestStop = useCallback(() => {
    if (!active || pending || logging) return;
    const endedAt = new Date().toISOString();
    const seconds = Math.max(
      1,
      Math.floor((Date.now() - new Date(active.startedAt).getTime()) / 1000)
    );
    setPending({
      issueKey: active.issueKey,
      title: active.title,
      startedAt: active.startedAt,
      endedAt,
      seconds
    });
    setElapsed(seconds);
    setLastMessage(null);
  }, [active, pending, logging]);

  /** Resume the running timer without logging. */
  const cancelPending = useCallback(() => {
    setPending(null);
  }, []);

  /** Save local + Jira worklog with the user's description. */
  const confirmLog = useCallback(
    async (comment: string) => {
      if (!pending) return null;
      const description = comment.trim();
      if (!description) {
        setLastMessage("Enter a log description before saving.");
        return null;
      }

      setLogging(true);
      setLastMessage(null);
      try {
        const res = await axios.post<WorklogResult>(
          `${API}/jira/worklog`,
          {
            issue_key: pending.issueKey,
            seconds: pending.seconds,
            started_at: pending.startedAt,
            ended_at: pending.endedAt,
            comment: description,
            push_to_jira: true
          },
          { headers: jiraAuthHeaders() }
        );
        if (res.data.totals) setTotals(res.data.totals);
        saveTimer(null);
        setActive(null);
        setPending(null);
        const mins = Math.max(1, Math.round(pending.seconds / 60));
        setLastMessage(
          res.data.local?.pushed_to_jira
            ? `Logged ${mins}m to ${pending.issueKey} (local + Jira)`
            : `Logged ${mins}m to ${pending.issueKey} (local only)`
        );
        return res.data;
      } catch (err: any) {
        // Keep pending open so the user can fix/retry with the same description.
        setLastMessage(
          err?.response?.data?.detail ||
            err?.message ||
            "Failed to log work time"
        );
        return null;
      } finally {
        setLogging(false);
      }
    },
    [pending]
  );

  const discard = useCallback(() => {
    saveTimer(null);
    setActive(null);
    setPending(null);
    setLastMessage("Timer discarded");
  }, []);

  return {
    active,
    pending,
    elapsed,
    logging,
    totals,
    setTotals,
    lastMessage,
    start,
    requestStop,
    cancelPending,
    confirmLog,
    discard,
    formatDuration
  };
}
