"use client";

import {
  BoardColumn,
  SelectedTicket,
  TaskLayer,
  ticketProgress
} from "../types";

const priorityStyles: Record<string, string> = {
  Highest: "bg-red-100 text-red-800 border-red-200",
  High: "bg-orange-100 text-orange-800 border-orange-200",
  Medium: "bg-yellow-100 text-yellow-800 border-yellow-200",
  Low: "bg-slate-100 text-slate-700 border-slate-200",
  Done: "bg-emerald-100 text-emerald-800 border-emerald-200"
};

const layerMeta: Record<
  TaskLayer,
  { title: string; color: string; border: string }
> = {
  frontend: {
    title: "Frontend tasks",
    color: "text-sky-800",
    border: "border-sky-200 bg-sky-50/50"
  },
  backend: {
    title: "Backend tasks",
    color: "text-violet-800",
    border: "border-violet-200 bg-violet-50/50"
  },
  db: {
    title: "Database tasks",
    color: "text-amber-800",
    border: "border-amber-200 bg-amber-50/50"
  }
};

type Props = {
  ticket: SelectedTicket | null;
  onClose: () => void;
  onToggleLayerTask: (layer: TaskLayer, taskIndex: number) => void;
  onToggleCriterion: (index: number) => void;
  onMarkComplete: () => void;
};

export default function TicketDetailModal({
  ticket,
  onClose,
  onToggleLayerTask,
  onToggleCriterion,
  onMarkComplete
}: Props) {
  if (!ticket) return null;

  const { story, moduleName, column, completedLayerTasks, completedCriteria } =
    ticket;
  const priority = (
    column === "Done" ? "Done" : story.priority || "Medium"
  ) as BoardColumn;
  const { percent, isComplete } = ticketProgress(ticket);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div className="absolute inset-0 bg-slate-900/60 backdrop-blur-sm" />

      <div
        className="relative z-10 flex max-h-[92vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b border-slate-200 bg-slate-50 px-6 py-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-indigo-600">
                {story.id} · {moduleName}
              </p>
              <h2 className="mt-1 text-xl font-bold text-slate-900">
                {story.title}
              </h2>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg p-2 text-slate-500 hover:bg-slate-200"
              aria-label="Close"
            >
              ✕
            </button>
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span
              className={`rounded-full border px-3 py-1 text-xs font-semibold ${priorityStyles[priority]}`}
            >
              {priority}
            </span>
            <span className="rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1 text-xs font-semibold text-indigo-700">
              {percent}% complete
            </span>
            {story.jira_meta?.status_name && (
              <span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-semibold text-slate-700">
                {story.jira_meta.status_name}
              </span>
            )}
            {story.jira_meta?.assignee && (
              <span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-semibold text-slate-700">
                {story.jira_meta.assignee}
              </span>
            )}
            {story.jira_meta?.url && (
              <a
                href={story.jira_meta.url}
                target="_blank"
                rel="noreferrer"
                className="rounded-full border border-teal-200 bg-teal-50 px-3 py-1 text-xs font-semibold text-teal-800 hover:bg-teal-100"
              >
                Open in Jira ↗
              </a>
            )}
            {!isComplete && (
              <button
                type="button"
                onClick={onMarkComplete}
                className="rounded-full border border-emerald-300 bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-800 hover:bg-emerald-100"
              >
                Mark all complete
              </button>
            )}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6">
          <section>
            <h3 className="mb-2 text-sm font-bold uppercase text-slate-500">
              Description
            </h3>
            <p className="text-slate-700 leading-relaxed">
              {story.description || "See layer tasks below."}
            </p>
            {story.source_snippet && (
              <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
                <p className="text-xs font-semibold uppercase text-slate-500 mb-1">
                  From document
                </p>
                <p className="text-sm text-slate-600 italic">{story.source_snippet}</p>
              </div>
            )}
          </section>

          <KeysPanel story={story} />

          <section>
            <h3 className="mb-2 text-sm font-bold uppercase text-slate-500">
              Acceptance criteria
            </h3>
            <ul className="space-y-2">
              {(story.acceptance_criteria || []).map((item, i) => {
                const done = completedCriteria.includes(i);
                return (
                  <li key={i}>
                    <label className="flex cursor-pointer gap-3 rounded-lg border border-emerald-100 bg-emerald-50/50 px-3 py-2 text-sm">
                      <input
                        type="checkbox"
                        checked={done}
                        onChange={() => onToggleCriterion(i)}
                        className="mt-0.5"
                      />
                      <span className={done ? "line-through text-slate-500" : ""}>
                        {item}
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          </section>

          {(["frontend", "backend", "db"] as TaskLayer[]).map((layer) => {
            const tasks =
              layer === "frontend"
                ? story.frontend_tasks
                : layer === "backend"
                  ? story.backend_tasks
                  : story.db_tasks;
            if (!tasks?.length) return null;
            const meta = layerMeta[layer];
            const doneSet = completedLayerTasks[layer];

            return (
              <section key={layer}>
                <h3
                  className={`mb-3 text-sm font-bold uppercase ${meta.color}`}
                >
                  {meta.title} ({doneSet.length}/{tasks.length})
                </h3>
                <ul className="space-y-2">
                  {tasks.map((task, i) => {
                    const done = doneSet.includes(i);
                    return (
                      <li key={i}>
                        <label
                          className={`flex cursor-pointer items-start gap-3 rounded-lg border px-4 py-3 text-sm hover:opacity-90 ${meta.border}`}
                        >
                          <input
                            type="checkbox"
                            checked={done}
                            onChange={() => onToggleLayerTask(layer, i)}
                            className="mt-1"
                          />
                          <span
                            className={
                              done ? "line-through text-slate-500" : "text-slate-800"
                            }
                          >
                            {task}
                          </span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              </section>
            );
          })}
        </div>

        <div className="border-t border-slate-200 bg-slate-50 px-6 py-4 flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

function KeysPanel({ story }: { story: SelectedTicket["story"] }) {
  const fe = story.frontend_keys;
  const be = story.backend_keys;
  const db = story.db_schema;

  if (!fe && !be && !db?.fields?.length) return null;

  return (
    <section className="rounded-xl border border-slate-200 bg-slate-50 p-4 space-y-4">
      <h3 className="text-sm font-bold uppercase text-slate-500">
        Required keys & schema
      </h3>

      {fe && (
        <KeyGroup title="Frontend keys" items={[
          ...(fe.routes?.map((r) => `Route: ${r}`) || []),
          ...(fe.components?.map((c) => `Component: ${c}`) || []),
          ...(fe.state_keys?.map((k) => `State: ${k}`) || [])
        ]} />
      )}

      {be && (
        <KeyGroup title="Backend keys" items={[
          ...(be.env?.map((e) => `ENV: ${e}`) || []),
          ...(be.api_endpoints?.map((a) => `API: ${a}`) || []),
          ...(be.middleware?.map((m) => `Middleware: ${m}`) || [])
        ]} />
      )}

      {db?.fields && db.fields.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-bold uppercase text-amber-800">
            DB table: {db.table || "—"}
          </p>
          <div className="overflow-x-auto rounded-lg border border-amber-200 bg-white">
            <table className="min-w-full text-xs">
              <thead className="bg-amber-50 text-left text-amber-900">
                <tr>
                  <th className="px-3 py-2">Field</th>
                  <th className="px-3 py-2">Type</th>
                  <th className="px-3 py-2">Key</th>
                  <th className="px-3 py-2">Required</th>
                </tr>
              </thead>
              <tbody>
                {db.fields.map((f) => (
                  <tr key={f.name} className="border-t border-slate-100">
                    <td className="px-3 py-2 font-mono">{f.name}</td>
                    <td className="px-3 py-2">{f.type}</td>
                    <td className="px-3 py-2">{f.key || "—"}</td>
                    <td className="px-3 py-2">{f.required ? "✓" : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {db.required_keys && db.required_keys.length > 0 && (
            <p className="mt-2 text-xs text-slate-600">
              Required keys:{" "}
              <span className="font-mono">{db.required_keys.join(", ")}</span>
            </p>
          )}
        </div>
      )}
    </section>
  );
}

function KeyGroup({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <p className="mb-1 text-xs font-bold uppercase text-slate-600">{title}</p>
      <div className="flex flex-wrap gap-1.5">
        {items.map((item) => (
          <span
            key={item}
            className="rounded-md border border-slate-200 bg-white px-2 py-1 font-mono text-xs text-slate-700"
          >
            {item}
          </span>
        ))}
      </div>
    </div>
  );
}
