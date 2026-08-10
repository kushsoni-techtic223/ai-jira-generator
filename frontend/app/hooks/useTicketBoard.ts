"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BoardColumn,
  BoardTicket,
  BoardView,
  JiraData,
  JiraWorkflowStatus,
  LayerCompletion,
  PRIORITY_COLUMNS,
  PRIORITY_ORDER,
  Priority,
  StoryStatus,
  TaskLayer,
  buildJiraStatusColumns,
  getLayerTasks,
  isJiraStatusDone,
  isJiraStatusInProgress
} from "../types";

function storageKey(project: string, source?: string) {
  const slug = project.replace(/\s+/g, "-").toLowerCase();
  return source === "jira" ? `jira-live-v1-${slug}` : `jira-board-v2-${slug}`;
}

function emptyLayers(): LayerCompletion {
  return { frontend: [], backend: [], db: [] };
}

function toColumn(story: BoardTicket["story"]): BoardColumn {
  if (story.status === "done") return "Done";
  return story.priority || "Medium";
}

function buildTickets(data: JiraData): BoardTicket[] {
  const tickets: BoardTicket[] = [];
  data.modules?.forEach((mod, moduleIndex) => {
    mod.stories?.forEach((story, storyIndex) => {
      tickets.push({
        story,
        moduleName: mod.name,
        moduleIndex,
        storyIndex,
        column: toColumn(story),
        completedLayerTasks: emptyLayers(),
        completedCriteria: []
      });
    });
  });
  return tickets;
}

function allLayerIndices(story: BoardTicket["story"], layer: TaskLayer) {
  return getLayerTasks(story, layer).map((_, i) => i);
}

export function useTicketBoard(data: JiraData) {
  const [tickets, setTickets] = useState<BoardTicket[]>(() => buildTickets(data));
  const [view, setView] = useState<BoardView>(
    data.source === "jira" ? "jira_status" : "priority"
  );
  const [draggingId, setDraggingId] = useState<string | null>(null);

  useEffect(() => {
    const key = storageKey(data.project_name, data.source);
    const fresh = buildTickets(data);

    // For live Jira data, always reset from latest server payload
    // when switching/reloading project sprint data.
    if (data.source === "jira") {
      setTickets(fresh);
      return;
    }

    const saved = localStorage.getItem(key);

    if (saved) {
      try {
        const parsed = JSON.parse(saved) as BoardTicket[];
        const byId = new Map(parsed.map((t) => [t.story.id, t]));
        setTickets(
          fresh.map((t) => {
            const prev = byId.get(t.story.id);
            if (!prev) return t;

            // Live Jira: keep checklist progress, but status/priority come from Jira
            if (data.source === "jira") {
              return {
                ...t,
                completedLayerTasks:
                  prev.completedLayerTasks || emptyLayers(),
                completedCriteria: prev.completedCriteria || []
              };
            }

            return {
              ...t,
              column: prev.column,
              completedLayerTasks:
                prev.completedLayerTasks || emptyLayers(),
              completedCriteria: prev.completedCriteria || [],
              story: {
                ...t.story,
                priority:
                  prev.column === "Done"
                    ? t.story.priority
                    : (prev.column as Priority),
                status:
                  prev.column === "Done"
                    ? ("done" as StoryStatus)
                    : ("todo" as StoryStatus)
              }
            };
          })
        );
        return;
      } catch {
        /* use fresh */
      }
    }
    setTickets(fresh);
  }, [data]);

  useEffect(() => {
    if (data.source === "jira") return;
    localStorage.setItem(
      storageKey(data.project_name, data.source),
      JSON.stringify(tickets)
    );
  }, [tickets, data.project_name, data.source]);

  const moveTicket = useCallback((id: string, column: BoardColumn) => {
    setTickets((prev) =>
      prev.map((t) => {
        if (t.story.id !== id) return t;
        const isDone = column === "Done";
        return {
          ...t,
          column,
          story: {
            ...t.story,
            priority: isDone ? t.story.priority || "Medium" : (column as Priority),
            status: isDone ? "done" : "todo"
          }
        };
      })
    );
  }, []);

  const updateJiraStatus = useCallback(
    (id: string, statusName: string, statusCategory?: string) => {
      setTickets((prev) =>
        prev.map((t) => {
          if (t.story.id !== id) return t;
          const meta = {
            ...(t.story.jira_meta || {}),
            status_name: statusName,
            ...(statusCategory ? { status_category: statusCategory } : {})
          };
          const done = isJiraStatusDone(meta, statusName);
          const inProgress = isJiraStatusInProgress(meta, statusName);
          return {
            ...t,
            column: done ? "Done" : t.column,
            story: {
              ...t.story,
              status: done ? "done" : inProgress ? "in_progress" : "todo",
              jira_meta: meta
            }
          };
        })
      );
    },
    []
  );

  const toggleLayerTask = useCallback(
    (id: string, layer: TaskLayer, taskIndex: number) => {
      setTickets((prev: BoardTicket[]) =>
        prev.map((t) => {
          if (t.story.id !== id) return t;
          const set = new Set(t.completedLayerTasks[layer]);
          if (set.has(taskIndex)) set.delete(taskIndex);
          else set.add(taskIndex);
          const completedLayerTasks = {
            ...t.completedLayerTasks,
            [layer]: [...set].sort((a: number, b: number) => a - b)
          };

          const fe = getLayerTasks(t.story, "frontend");
          const be = getLayerTasks(t.story, "backend");
          const db = getLayerTasks(t.story, "db");
          const totalAc = t.story.acceptance_criteria?.length || 0;
          const total = fe.length + be.length + db.length + totalAc;
          const allDone =
            total > 0 &&
            completedLayerTasks.frontend.length >= fe.length &&
            completedLayerTasks.backend.length >= be.length &&
            completedLayerTasks.db.length >= db.length &&
            t.completedCriteria.length >= totalAc;

          return {
            ...t,
            completedLayerTasks,
            column: allDone
              ? "Done"
              : t.column === "Done"
                ? "Medium"
                : t.column,
            story: {
              ...t.story,
              status: allDone ? "done" : "in_progress"
            }
          };
        })
      );
    },
    []
  );

  const toggleCriterion = useCallback((id: string, index: number) => {
    setTickets((prev: BoardTicket[]) =>
      prev.map((t: BoardTicket) => {
        if (t.story.id !== id) return t;
        const set = new Set(t.completedCriteria);
        if (set.has(index)) set.delete(index);
        else set.add(index);
        const completedCriteria = [...set].sort((a: number, b: number) => a - b);

        const fe = getLayerTasks(t.story, "frontend");
        const be = getLayerTasks(t.story, "backend");
        const db = getLayerTasks(t.story, "db");
        const totalAc = t.story.acceptance_criteria?.length || 0;
        const total = fe.length + be.length + db.length + totalAc;
        const allDone =
          total > 0 &&
          t.completedLayerTasks.frontend.length >= fe.length &&
          t.completedLayerTasks.backend.length >= be.length &&
          t.completedLayerTasks.db.length >= db.length &&
          completedCriteria.length >= totalAc;

        return {
          ...t,
          completedCriteria,
          column: allDone ? "Done" : t.column === "Done" ? "Medium" : t.column,
          story: {
            ...t.story,
            status: allDone ? "done" : "in_progress"
          }
        };
      })
    );
  }, []);

  const markComplete = useCallback((id: string) => {
    setTickets((prev: BoardTicket[]) =>
      prev.map((t) => {
        if (t.story.id !== id) return t;
        const acCount = t.story.acceptance_criteria?.length || 0;
        return {
          ...t,
          column: "Done",
          completedLayerTasks: {
            frontend: allLayerIndices(t.story, "frontend"),
            backend: allLayerIndices(t.story, "backend"),
            db: allLayerIndices(t.story, "db")
          },
          completedCriteria: Array.from({ length: acCount }, (_, i) => i),
          story: { ...t.story, status: "done" }
        };
      })
    );
  }, []);

  const ticketsByColumn = useMemo(() => {
    const map: Record<BoardColumn, BoardTicket[]> = {
      Highest: [],
      High: [],
      Medium: [],
      Low: [],
      Done: []
    };
    for (const t of tickets) {
      map[t.column].push(t);
    }
    for (const col of PRIORITY_COLUMNS) {
      map[col].sort(
        (a, b) => PRIORITY_ORDER[a.column] - PRIORITY_ORDER[b.column]
      );
    }
    return map;
  }, [tickets]);

  const ticketsByModule = useMemo(() => {
    const map = new Map<string, BoardTicket[]>();
    for (const t of tickets) {
      if (!map.has(t.moduleName)) map.set(t.moduleName, []);
      map.get(t.moduleName)!.push(t);
    }
    return map;
  }, [tickets]);

  const jiraStatusColumns = useMemo(
    () => buildJiraStatusColumns(data.jira_statuses, tickets),
    [data.jira_statuses, tickets]
  );

  const ticketsByJiraStatus = useMemo(() => {
    const map: Record<string, BoardTicket[]> = {};
    for (const s of jiraStatusColumns) {
      map[s.name] = [];
    }
    for (const t of tickets) {
      const name = t.story.jira_meta?.status_name?.trim() || "Unknown";
      if (!map[name]) map[name] = [];
      map[name].push(t);
    }
    return map;
  }, [tickets, jiraStatusColumns]);

  const completedCount = tickets.filter((t) => {
    if (data.source === "jira") {
      return isJiraStatusDone(
        t.story.jira_meta,
        t.story.jira_meta?.status_name
      );
    }
    return t.column === "Done";
  }).length;

  const workingNowCount = tickets.filter((t) =>
    isJiraStatusInProgress(t.story.jira_meta, t.story.jira_meta?.status_name)
  ).length;

  return {
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
  };
}
