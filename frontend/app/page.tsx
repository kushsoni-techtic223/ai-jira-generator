"use client";

import { useEffect, useState } from "react";

// Generate backlog (temporarily disabled)
// import axios from "axios";
// import UploadBox from "./components/UploadBox";
// import JiraBoard from "./components/JiraBoard";
// import { JiraData } from "./types";

import LiveJiraBoard from "./components/LiveJiraBoard";
import GitHubDailyBoard from "./components/GitHubDailyBoard";

type AppTab = "live" | "github";
// type AppTab = "generate" | "live" | "github";

const TIME_TABS: {
  id: "live" | "github";
  label: string;
  purpose: string;
  usefulFor: string;
}[] = [
  {
    id: "live",
    label: "Live Jira board",
    purpose:
      "Connect Jira, run timers on tickets, and build today’s task sheet from real worklogs.",
    usefulFor:
      "Useful when you track time against Jira issues and need a TL/PM/HR email with In/Out and totals.",
  },
  {
    id: "github",
    label: "GitHub daily",
    purpose:
      "Connect GitHub, pick projects assigned to you, and turn today’s commits into the same daily sheet.",
    usefulFor:
      "Useful when your day is commit-driven and you want a report without starting Jira timers.",
  },
];

export default function Home() {
  const [tab, setTab] = useState<AppTab>("live");

  // Generate backlog state (temporarily disabled)
  // const [data, setData] = useState<JiraData | null>(null);
  // const [loading, setLoading] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (
      params.get("tab") === "github" ||
      params.get("github") === "connected" ||
      params.get("gh_sid")
    ) {
      setTab("github");
    } else if (
      params.get("tab") === "live" ||
      params.get("jira") === "connected"
    ) {
      setTab("live");
    }
    // else if (params.get("tab") === "generate") {
    //   setTab("generate");
    // }
  }, []);

  /*
  const handleUpload = async (file: File) => {
    setLoading(true);
    setData(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const response = await axios.post<JiraData>(
        "http://localhost:8000/generate-jira",
        formData,
        { timeout: 0 }
      );
      setData(response.data);
    } catch (err: any) {
      setData({
        project_name: "Upload failed",
        modules: [],
        common_components: [],
        error:
          err?.code === "ECONNABORTED"
            ? "Request timed out. Restart backend and try again — large docs now use fast mode (~3–5 min)."
            : err?.response?.data?.detail ||
              err?.message ||
              "Could not reach backend at http://localhost:8000",
      });
    } finally {
      setLoading(false);
    }
  };
  */

  const activeTime = TIME_TABS.find((t) => t.id === tab);

  return (
    <main className="relative min-h-screen overflow-hidden">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10"
        style={{
          background: `
            radial-gradient(1200px 600px at 12% -10%, rgba(196, 92, 38, 0.18), transparent 55%),
            radial-gradient(900px 500px at 90% 8%, rgba(42, 48, 56, 0.12), transparent 50%),
            linear-gradient(165deg, #f7f5f1 0%, #eef1f4 48%, #e6ebe8 100%)
          `,
        }}
      />
      <div
        aria-hidden
        className="dtl-glow pointer-events-none absolute -left-24 top-10 -z-10 h-[28rem] w-[28rem] rounded-full opacity-60 blur-3xl"
        style={{
          background:
            "radial-gradient(circle, rgba(196,92,38,0.28) 0%, transparent 70%)",
        }}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10 opacity-[0.35]"
        style={{
          backgroundImage: `
            linear-gradient(rgba(18,21,26,0.04) 1px, transparent 1px),
            linear-gradient(90deg, rgba(18,21,26,0.04) 1px, transparent 1px)
          `,
          backgroundSize: "48px 48px",
          maskImage:
            "linear-gradient(to bottom, black 0%, black 42%, transparent 78%)",
        }}
      />

      <div className="mx-auto max-w-7xl px-4 pb-20 pt-8 sm:px-8 sm:pt-12">
        <header className="mb-10 max-w-3xl">
          <p className="dtl-rise font-display text-xs font-semibold uppercase tracking-[0.28em] text-[var(--dtl-copper)]">
            Daily Time Logger
          </p>
          <h1 className="dtl-rise-delay font-display mt-3 text-5xl font-bold leading-[0.95] tracking-tight text-[var(--dtl-ink)] sm:text-6xl md:text-7xl">
            Daily Time
            <br />
            <span className="text-[var(--dtl-ink-soft)]">Logger</span>
          </h1>
          <p className="dtl-rise-delay-2 mt-5 max-w-xl text-base leading-relaxed text-[var(--dtl-ink-soft)] sm:text-lg">
            Built so engineers can prove what they worked on today — from Jira
            timers or GitHub commits — and email a clear sheet to TL / PM / HR.
          </p>
        </header>

        <section className="dtl-rise-delay-2 mb-12 grid gap-4 sm:grid-cols-2">
          <PurposeTile
            kicker="Why it exists"
            title="End-of-day reporting"
            body="Managers need a consistent Today Task sheet. This app builds it from work you already did — timers or commits — so you don’t rewrite the day by hand."
          />
          <PurposeTile
            kicker="Who it’s for"
            title="Engineers → TL / PM / HR"
            body="You log or commit as usual. At day’s end you preview the sheet, open Mail or Gmail, and send. Recipients stay configured in the backend."
          />
        </section>

        <section className="mb-14">
          <div className="mb-5">
            <p className="font-display text-xs font-semibold uppercase tracking-[0.22em] text-[var(--dtl-copper)]">
              Today&apos;s time
            </p>
            <h2 className="font-display mt-2 text-2xl font-bold text-[var(--dtl-ink)] sm:text-3xl">
              Log what you worked on
            </h2>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[var(--dtl-ink-soft)]">
              Two ways to fill the same daily sheet. Pick the source that matches
              how you actually work.
            </p>
          </div>

          <nav
            aria-label="Time sources"
            className="mb-4 flex w-full max-w-xl flex-wrap gap-1 border-b border-[var(--dtl-line)]"
          >
            {TIME_TABS.map((t) => (
              <TabBtn
                key={t.id}
                active={tab === t.id}
                onClick={() => setTab(t.id)}
              >
                {t.label}
              </TabBtn>
            ))}
          </nav>

          {activeTime && (
            <div className="mb-6 max-w-2xl rounded-2xl border border-[var(--dtl-line)] bg-white/60 px-4 py-3 backdrop-blur-sm">
              <p className="text-sm font-medium text-[var(--dtl-ink)]">
                {activeTime.purpose}
              </p>
              <p className="mt-1 text-sm text-[var(--dtl-ink-soft)]">
                {activeTime.usefulFor}
              </p>
            </div>
          )}

          <div className="dtl-fade-in">
            {tab === "live" ? <LiveJiraBoard /> : <GitHubDailyBoard />}
          </div>
        </section>

        {/*
        ============================================================
        Generate backlog — temporarily commented out
        ============================================================
        <section
          id="generate-backlog"
          className="relative overflow-hidden rounded-[1.75rem] border border-[var(--dtl-ink)]/10 bg-[var(--dtl-ink)] text-[var(--dtl-paper)] shadow-[0_30px_80px_-40px_rgba(18,21,26,0.65)]"
        >
          ... Open Generate backlog UI (UploadBox + JiraBoard) ...
        </section>
        */}
      </div>
    </main>
  );
}

function PurposeTile({
  kicker,
  title,
  body,
}: {
  kicker: string;
  title: string;
  body: string;
}) {
  return (
    <div className="rounded-2xl border border-[var(--dtl-line)] bg-white/65 p-5 backdrop-blur-sm">
      <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-[var(--dtl-copper)]">
        {kicker}
      </p>
      <h3 className="font-display mt-2 text-lg font-bold text-[var(--dtl-ink)]">
        {title}
      </h3>
      <p className="mt-2 text-sm leading-relaxed text-[var(--dtl-ink-soft)]">
        {body}
      </p>
    </div>
  );
}

function TabBtn({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`relative px-4 py-3 text-sm font-semibold transition ${
        active
          ? "text-[var(--dtl-ink)]"
          : "text-[var(--dtl-ink-soft)] hover:text-[var(--dtl-ink)]"
      }`}
    >
      {children}
      <span
        aria-hidden
        className={`absolute inset-x-3 -bottom-px h-0.5 origin-left rounded-full bg-[var(--dtl-copper)] transition-transform duration-300 ${
          active ? "scale-x-100" : "scale-x-0"
        }`}
      />
    </button>
  );
}
