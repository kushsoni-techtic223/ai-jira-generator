"use client";

import { useMemo, useState } from "react";
import TicketCard from "./TicketCard.tsx";
import TicketDetailModal from "./TicketDetailModal.tsx";
import SharedComponentsPanel from "./SharedComponentsPanel";
import { useTicketBoard } from "../hooks/useTicketBoard";
import {
  BoardColumn,
  countStoryTasks,
  JiraData,
  PRIORITY_COLUMNS,
  SelectedTicket,
  normalizeComponents
} from "../types";

const columnStyles: Record<BoardColumn, string> = {
  Highest: "border-t-red-500 bg-red-50/40",
  High: "border-t-orange-500 bg-orange-50/40",
  Medium: "border-t-yellow-500 bg-yellow-50/40",
  Low: "border-t-slate-400 bg-slate-50/40",
  Done: "border-t-emerald-500 bg-emerald-50/40"
};

type Props = {
  data: JiraData;
};

export default function JiraBoard({ data }: Props) {
  const {
    tickets,
    ticketsByColumn,
    ticketsByModule,
    view,
    setView,
    draggingId,
    setDraggingId,
    moveTicket,
    toggleLayerTask,
    toggleCriterion,
    markComplete,
    completedCount
  } = useTicketBoard(data);

  const [selected, setSelected] = useState<SelectedTicket | null>(null);

  const selectedLive = useMemo(() => {
    if (!selected) return null;
    return tickets.find((t) => t.story.id === selected.story.id) || selected;
  }, [selected, tickets]);

  const stats = useMemo(() => {
    const components =
      data.stats?.components ??
      normalizeComponents(data.common_components).length;
    return {
      modules: data.stats?.modules ?? data.modules?.length ?? 0,
      stories: tickets.length,
      tasks: tickets.reduce((n, t) => n + countStoryTasks(t.story), 0),
      components,
      completed: completedCount
    };
  }, [data, tickets, completedCount]);

  const handleDrop = (column: BoardColumn, e: React.DragEvent) => {
    e.preventDefault();
    const id = e.dataTransfer.getData("text/plain");
    if (id) moveTicket(id, column);
    setDraggingId(null);
  };

  return (
    <>
      <div className="mt-10">
        <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-sm font-semibold uppercase tracking-wide text-indigo-600">
              Project backlog
            </p>
            <h2 className="text-3xl font-bold text-slate-900">
              {data.project_name}
            </h2>
          </div>

          <div className="flex flex-wrap gap-3">
            <StatPill label="Stories" value={stats.stories} />
            <StatPill label="Done" value={stats.completed} />
            <StatPill label="Tasks" value={stats.tasks} />
            <StatPill label="Components" value={stats.components} />
          </div>
        </div>

        <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-slate-500">
            Drag tickets between priority columns. Check off tasks & acceptance
            criteria to complete each ticket.
          </p>
          <div className="flex rounded-lg border border-slate-200 bg-white p-1">
            <ViewBtn active={view === "priority"} onClick={() => setView("priority")}>
              By priority
            </ViewBtn>
            <ViewBtn active={view === "module"} onClick={() => setView("module")}>
              By module
            </ViewBtn>
          </div>
        </div>

        {view === "priority" ? (
          <div className="flex gap-4 overflow-x-auto pb-4">
            {PRIORITY_COLUMNS.map((column) => (
              <div
                key={column}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => handleDrop(column, e)}
                className={`flex w-80 shrink-0 flex-col rounded-xl border-t-4 p-3 ${columnStyles[column]} ${
                  draggingId ? "ring-2 ring-indigo-200 ring-offset-2" : ""
                }`}
              >
                <div className="mb-3 flex items-center justify-between px-1">
                  <h3 className="font-bold text-slate-800">{column}</h3>
                  <span className="rounded-full bg-white/80 px-2 py-0.5 text-xs font-semibold text-slate-700">
                    {ticketsByColumn[column].length}
                  </span>
                </div>

                <div className="flex-1 space-y-0 overflow-y-auto max-h-[70vh] pr-1">
                  {ticketsByColumn[column].map((ticket) => (
                    <TicketCard
                      key={ticket.story.id}
                      ticket={ticket}
                      onOpen={() => setSelected(ticket)}
                      onDragStart={() => setDraggingId(ticket.story.id)}
                      onDragEnd={() => setDraggingId(null)}
                      onMarkComplete={() => markComplete(ticket.story.id)}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : (
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
                <div className="flex-1 space-y-0 overflow-y-auto max-h-[70vh] pr-1">
                  {moduleTickets.map((ticket) => (
                    <TicketCard
                      key={ticket.story.id}
                      ticket={ticket}
                      onOpen={() => setSelected(ticket)}
                      onDragStart={() => setDraggingId(ticket.story.id)}
                      onDragEnd={() => setDraggingId(null)}
                      onMarkComplete={() => markComplete(ticket.story.id)}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}

        <SharedComponentsPanel data={data} />
      </div>

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
        active
          ? "bg-indigo-600 text-white"
          : "text-slate-600 hover:bg-slate-100"
      }`}
    >
      {children}
    </button>
  );
}
