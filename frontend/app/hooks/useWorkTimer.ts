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
    if (!active) {
      setElapsed(0);
      return;
    }
    const tick = () => {
      const start = new Date(active.startedAt).getTime();
      setElapsed(Math.floor((Date.now() - start) / 1000));
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [active]);

  const start = useCallback((issueKey: string, title: string) => {
    const timer: ActiveTimer = {
      issueKey,
      title,
      startedAt: new Date().toISOString()
    };
    saveTimer(timer);
    setActive(timer);
    setLastMessage(null);
  }, []);

  const stopAndLog = useCallback(
    async (opts?: { pushToJira?: boolean; comment?: string }) => {
      if (!active) return null;
      const endedAt = new Date().toISOString();
      const seconds = Math.max(
        1,
        Math.floor((Date.now() - new Date(active.startedAt).getTime()) / 1000)
      );
      setLogging(true);
      setLastMessage(null);
      try {
        const res = await axios.post<WorklogResult>(
          `${API}/jira/worklog`,
          {
            issue_key: active.issueKey,
            seconds,
            started_at: active.startedAt,
            ended_at: endedAt,
            comment:
              opts?.comment ||
              `Timer session on ${active.issueKey}: ${active.title}`,
            push_to_jira: opts?.pushToJira !== false
          },
          { headers: jiraAuthHeaders() }
        );
        if (res.data.totals) setTotals(res.data.totals);
        saveTimer(null);
        setActive(null);
        const mins = Math.max(1, Math.round(seconds / 60));
        setLastMessage(
          res.data.local?.pushed_to_jira
            ? `Logged ${mins}m to ${active.issueKey} (local + Jira)`
            : `Logged ${mins}m to ${active.issueKey} (local only)`
        );
        return res.data;
      } catch (err: any) {
        // Still keep local if Jira push failed after local save? API rolls together.
        // Keep timer running on failure so user can retry.
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
    [active]
  );

  const discard = useCallback(() => {
    saveTimer(null);
    setActive(null);
    setLastMessage("Timer discarded");
  }, []);

  return {
    active,
    elapsed,
    logging,
    totals,
    setTotals,
    lastMessage,
    start,
    stopAndLog,
    discard,
    formatDuration
  };
}
