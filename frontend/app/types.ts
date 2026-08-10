export type Priority = "Highest" | "High" | "Medium" | "Low";
export type BoardColumn = Priority | "Done";
export type JiraStatusColumn =
  | "To Do"
  | "Working now"
  | "Dev done"
  | "Done after QA verified"
  | "Other";
export type BoardView = "priority" | "module" | "jira_status";
export type StoryStatus = "todo" | "in_progress" | "done";
export type TaskLayer = "frontend" | "backend" | "db";

export type DbField = {
  name: string;
  type: string;
  required: boolean;
  key?: string;
};

export type DbSchema = {
  table?: string;
  fields?: DbField[];
  required_keys?: string[];
};

export type LayerKeys = {
  routes?: string[];
  components?: string[];
  state_keys?: string[];
  env?: string[];
  api_endpoints?: string[];
  middleware?: string[];
};

export type JiraMeta = {
  status_name?: string;
  status_id?: string;
  status_category?: string;
  assignee?: string;
  issue_type?: string;
  updated?: string;
  created?: string;
  duedate?: string | null;
  url?: string;
};

/** Real workflow status from the project's Jira board */
export type JiraWorkflowStatus = {
  id: string;
  name: string;
  category?: string;
  category_name?: string;
  column_name?: string;
  order?: number;
};

export type Story = {
  id: string;
  title: string;
  description?: string;
  acceptance_criteria?: string[];
  priority?: Priority;
  story_points?: number;
  labels?: string[];
  tasks: string[];
  frontend_tasks?: string[];
  backend_tasks?: string[];
  db_tasks?: string[];
  frontend_keys?: LayerKeys;
  backend_keys?: LayerKeys;
  db_schema?: DbSchema;
  module_name?: string;
  source_snippet?: string | null;
  status?: StoryStatus;
  jira_meta?: JiraMeta;
};

export type Module = {
  name: string;
  stories: Story[];
};

export type SharedComponent = {
  name: string;
  description: string;
  file_path: string;
  language: string;
  code: string;
};

export type JiraData = {
  project_name: string;
  modules: Module[];
  common_components: SharedComponent[] | string[];
  error?: string;
  warning?: string;
  source?: "ai" | "jira";
  mode?: string;
  jql?: string | null;
  logged_seconds?: Record<string, number>;
  fetched?: number;
  max_results?: number;
  may_have_more?: boolean;
  stats?: {
    modules: number;
    stories: number;
    tasks: number;
    components?: number;
  };
  /** Ordered statuses from the project's Jira board workflow */
  jira_statuses?: JiraWorkflowStatus[];
};

export type JiraConnection = {
  base_url: string;
  email: string;
  api_token: string;
  project_key: string;
  board_id: string;
};

export type JiraFetchMode = "board" | "today" | "my_open" | "project";

export type LayerCompletion = {
  frontend: number[];
  backend: number[];
  db: number[];
};

export type BoardTicket = {
  story: Story;
  moduleName: string;
  moduleIndex: number;
  storyIndex: number;
  column: BoardColumn;
  completedLayerTasks: LayerCompletion;
  completedCriteria: number[];
};

export type SelectedTicket = BoardTicket;

export const PRIORITY_COLUMNS: BoardColumn[] = [
  "Highest",
  "High",
  "Medium",
  "Low",
  "Done"
];

export const PRIORITY_ORDER: Record<BoardColumn, number> = {
  Highest: 0,
  High: 1,
  Medium: 2,
  Low: 3,
  Done: 4
};

/** @deprecated Use dynamic jira_statuses from API instead */
export const JIRA_STATUS_COLUMNS: JiraStatusColumn[] = [
  "To Do",
  "Working now",
  "Dev done",
  "Done after QA verified",
  "Other"
];

/** Build ordered status columns: board workflow + any extra statuses on tickets */
export function buildJiraStatusColumns(
  workflow: JiraWorkflowStatus[] | undefined,
  tickets: { story: Story }[]
): JiraWorkflowStatus[] {
  const cols: JiraWorkflowStatus[] = [...(workflow || [])];
  const known = new Set(cols.map((s) => s.name));
  let order = cols.length;

  for (const t of tickets) {
    const name = t.story.jira_meta?.status_name?.trim();
    if (!name || known.has(name)) continue;
    known.add(name);
    cols.push({
      id: t.story.jira_meta?.status_id || name,
      name,
      category: t.story.jira_meta?.status_category,
      order: order++
    });
  }

  if (!cols.length) {
    const fromTickets = new Map<string, JiraWorkflowStatus>();
    for (const t of tickets) {
      const name = t.story.jira_meta?.status_name?.trim();
      if (!name || fromTickets.has(name)) continue;
      fromTickets.set(name, {
        id: t.story.jira_meta?.status_id || name,
        name,
        category: t.story.jira_meta?.status_category,
        order: fromTickets.size
      });
    }
    return [...fromTickets.values()];
  }

  return cols.sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
}

export function isJiraStatusDone(
  meta?: JiraMeta | null,
  statusName?: string | null
): boolean {
  const cat = (meta?.status_category || "").toLowerCase();
  if (cat === "done") return true;
  const name = (statusName || meta?.status_name || "").trim().toLowerCase();
  return name === "done" || name.startsWith("done ");
}

export function isJiraStatusInProgress(
  meta?: JiraMeta | null,
  statusName?: string | null
): boolean {
  const cat = (meta?.status_category || "").toLowerCase();
  if (cat === "indeterminate") return true;
  const name = (statusName || meta?.status_name || "").trim().toLowerCase();
  return (
    name.includes("progress") ||
    name.includes("working") ||
    name.includes("qa") ||
    name.includes("dev completed") ||
    name.includes("ready for")
  );
}

/** Tailwind classes matching Jira status category colors */
export function jiraStatusColumnStyle(category?: string): string {
  const c = (category || "").toLowerCase();
  if (c === "done") return "border-t-emerald-500 bg-emerald-50/40";
  if (c === "indeterminate") return "border-t-blue-500 bg-blue-50/40";
  if (c === "new") return "border-t-slate-400 bg-slate-50/50";
  return "border-t-violet-400 bg-violet-50/40";
}

export function jiraStatusPillStyle(category?: string): string {
  const c = (category || "").toLowerCase();
  if (c === "done") return "bg-emerald-100 text-emerald-800";
  if (c === "indeterminate") return "bg-blue-100 text-blue-800";
  if (c === "new") return "bg-slate-200 text-slate-700";
  return "bg-violet-100 text-violet-800";
}

/** Map real Jira status names → legacy board columns (AI-generated boards) */
const JIRA_STATUS_ALIASES: Record<string, JiraStatusColumn> = {
  "to do": "To Do",
  todo: "To Do",
  open: "To Do",
  backlog: "To Do",
  "in progress": "Working now",
  "qa in progress": "Working now",
  working: "Working now",
  "working now": "Working now",
  "dev completed": "Dev done",
  "dev done": "Dev done",
  "ready for qa": "Dev done",
  "waiting for api": "Dev done",
  done: "Done after QA verified",
  "done after qa verified": "Done after QA verified",
  "done after qa": "Done after QA verified"
};

/** Preferred Jira status to transition into when dropping on a column */
export const JIRA_STATUS_TARGET: Record<
  Exclude<JiraStatusColumn, "Other">,
  string
> = {
  "To Do": "To Do",
  "Working now": "In Progress",
  "Dev done": "Dev Completed",
  "Done after QA verified": "Done"
};

export function mapJiraStatusToColumn(
  statusName?: string | null
): JiraStatusColumn {
  if (!statusName) return "Other";
  return JIRA_STATUS_ALIASES[statusName.trim().toLowerCase()] || "Other";
}

export function getLayerTasks(story: Story, layer: TaskLayer): string[] {
  if (layer === "frontend") return story.frontend_tasks || [];
  if (layer === "backend") return story.backend_tasks || [];
  return story.db_tasks || [];
}

export function countStoryTasks(story: Story): number {
  return (
    (story.frontend_tasks?.length || 0) +
    (story.backend_tasks?.length || 0) +
    (story.db_tasks?.length || 0)
  );
}

export function normalizeComponents(
  raw: JiraData["common_components"]
): SharedComponent[] {
  if (!raw?.length) return [];

  return raw.map((item, index) => {
    if (typeof item === "string") {
      const name = item.trim();
      const pascal = name
        .replace(/[^a-zA-Z0-9]+/g, " ")
        .trim()
        .split(" ")
        .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
        .join("");
      return {
        name,
        description: `Shared component: ${name}`,
        file_path: `src/components/shared/${pascal || "Component"}.tsx`,
        language: "typescript",
        code: `// ${name}\nexport function ${pascal || "Component"}() {\n  return null;\n}`
      };
    }
    return {
      name: item.name || `Component${index + 1}`,
      description: item.description || "",
      file_path:
        item.file_path || `src/components/shared/Component${index + 1}.tsx`,
      language: item.language || "typescript",
      code: item.code || "// No code generated"
    };
  });
}

export function ticketProgress(ticket: BoardTicket) {
  const fe = getLayerTasks(ticket.story, "frontend");
  const be = getLayerTasks(ticket.story, "backend");
  const db = getLayerTasks(ticket.story, "db");
  const totalTasks = fe.length + be.length + db.length;
  const totalAc = ticket.story.acceptance_criteria?.length || 0;
  const total = totalTasks + totalAc;

  const jiraDone =
    ticket.column === "Done" ||
    ticket.story.status === "done" ||
    isJiraStatusDone(ticket.story.jira_meta, ticket.story.jira_meta?.status_name);

  const done =
    ticket.completedLayerTasks.frontend.length +
    ticket.completedLayerTasks.backend.length +
    ticket.completedLayerTasks.db.length +
    ticket.completedCriteria.length;

  const checklistComplete =
    total > 0 &&
    ticket.completedLayerTasks.frontend.length >= fe.length &&
    ticket.completedLayerTasks.backend.length >= be.length &&
    ticket.completedLayerTasks.db.length >= db.length &&
    ticket.completedCriteria.length >= totalAc;

  // If any checklist item exists and isn't done → unchecked.
  // Jira Done only marks complete when there are no inner points.
  const isComplete = total > 0 ? checklistComplete : jiraDone;

  const percent =
    total > 0
      ? Math.round((done / total) * 100)
      : isComplete
        ? 100
        : 0;

  return {
    total,
    done,
    percent,
    isComplete,
    jiraDone,
    checklistComplete,
    totalTasks,
    totalAc,
    fe: fe.length,
    be: be.length,
    db: db.length
  };
}
