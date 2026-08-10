"use client";

import { BoardTicket, ticketProgress } from "../types";
import { formatDuration } from "../hooks/useWorkTimer";

const priorityDot: Record<string, string> = {
  Highest: "bg-red-500",
  High: "bg-orange-500",
  Medium: "bg-yellow-500",
  Low: "bg-slate-400",
  Done: "bg-emerald-500"
};

type Props = {
  ticket: BoardTicket;
  onOpen: () => void;
  onDragStart: () => void;
  onDragEnd: () => void;
  onMarkComplete: () => void;
  loggedSeconds?: number;
  timerActive?: boolean;
  timerElapsed?: number;
  onStartTimer?: () => void;
  onStopTimer?: () => void;
  timerBusy?: boolean;
};

export default function TicketCard({
  ticket,
  onOpen,
  onDragStart,
  onDragEnd,
  onMarkComplete,
  loggedSeconds = 0,
  timerActive = false,
  timerElapsed = 0,
  onStartTimer,
  onStopTimer,
  timerBusy = false
}: Props) {
  const { story, moduleName, column } = ticket;
  const priority = column === "Done" ? "Done" : story.priority || "Medium";
  const { percent, done, total, isComplete, fe, be, db } =
    ticketProgress(ticket);

  return (
    <div
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData("text/plain", story.id);
        e.dataTransfer.effectAllowed = "move";
        onDragStart();
      }}
      onDragEnd={onDragEnd}
      className={`group mb-3 w-full rounded-xl border bg-white p-4 text-left shadow-sm transition hover:shadow-md ${
        timerActive
          ? "border-teal-400 ring-2 ring-teal-100"
          : isComplete
            ? "border-emerald-200 bg-emerald-50/30"
            : "border-slate-200 hover:border-indigo-300"
      }`}
    >
      <div className="flex items-start gap-2">
        <span
          className="mt-1 cursor-grab text-slate-400 active:cursor-grabbing"
          title="Drag to change priority"
        >
          ⠿
        </span>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onMarkComplete();
          }}
          className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded border ${
            isComplete
              ? "border-emerald-500 bg-emerald-500 text-white"
              : "border-slate-300 bg-white hover:border-indigo-400"
          }`}
          title="Mark complete"
        >
          {isComplete ? "✓" : ""}
        </button>
        <div className="min-w-0 flex-1">
          <button type="button" onClick={onOpen} className="w-full text-left">
            <div className="flex items-start gap-2">
              <span
                className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${priorityDot[priority] || priorityDot.Medium}`}
              />
              <div className="min-w-0 flex-1">
              <p className="text-xs font-medium text-slate-400">
                {story.id} · {moduleName}
                {story.jira_meta?.status_name
                  ? ` · ${story.jira_meta.status_name}`
                  : ""}
              </p>
                <h4
                  className={`font-semibold line-clamp-2 ${
                    isComplete
                      ? "text-slate-500 line-through"
                      : "text-slate-900 group-hover:text-indigo-700"
                  }`}
                >
                  {story.title}
                </h4>
              </div>
            </div>

            {story.description && (
              <p className="mt-2 text-xs text-slate-500 line-clamp-2">
                {story.description}
              </p>
            )}

            <div className="mt-3 flex flex-wrap gap-1.5">
              <LayerBadge
                label="FE"
                count={fe}
                color="bg-sky-100 text-sky-800"
              />
              <LayerBadge
                label="BE"
                count={be}
                color="bg-violet-100 text-violet-800"
              />
              <LayerBadge
                label="DB"
                count={db}
                color="bg-amber-100 text-amber-800"
              />
              {loggedSeconds > 0 && (
                <span className="rounded bg-teal-100 px-2 py-0.5 text-xs font-semibold text-teal-800">
                  Logged {formatDuration(loggedSeconds)}
                </span>
              )}
            </div>

            <div className="mt-3">
              <div className="mb-1 flex justify-between text-xs text-slate-500">
                <span>
                  {done}/{total} completable items
                </span>
                <span>{percent}%</span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-slate-200">
                <div
                  className={`h-full transition-all ${
                    isComplete ? "bg-emerald-500" : "bg-indigo-500"
                  }`}
                  style={{ width: `${percent}%` }}
                />
              </div>
            </div>
          </button>

          {onStartTimer && onStopTimer && (
            <div className="mt-3 flex items-center gap-2 border-t border-slate-100 pt-3">
              {timerActive ? (
                <>
                  <span className="font-mono text-sm font-bold tabular-nums text-teal-800">
                    {formatDuration(timerElapsed)}
                  </span>
                  <button
                    type="button"
                    disabled={timerBusy}
                    onClick={(e) => {
                      e.stopPropagation();
                      onStopTimer();
                    }}
                    className="ml-auto rounded-md bg-teal-700 px-2.5 py-1 text-xs font-semibold text-white hover:bg-teal-800 disabled:opacity-60"
                  >
                    {timerBusy ? "Logging…" : "Stop & log"}
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onStartTimer();
                  }}
                  className="rounded-md border border-teal-200 bg-teal-50 px-2.5 py-1 text-xs font-semibold text-teal-800 hover:bg-teal-100"
                >
                  ▶ Start work
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function LayerBadge({
  label,
  count,
  color
}: {
  label: string;
  count: number;
  color: string;
}) {
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-semibold ${color}`}>
      {label} {count}
    </span>
  );
}
