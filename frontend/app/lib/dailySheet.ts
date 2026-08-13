/** Manual / sheet helpers for the daily task email preview. */

export type SheetRow = {
  sr: number;
  date: string;
  date_display?: string;
  in_time: string;
  out_time: string;
  total_time: string;
  project: string;
  task: string;
  task_summary?: string;
  task_url?: string | null;
  manual?: boolean;
};

export type EditableSheetRow = SheetRow & {
  id: string;
};

export type ManualDraft = {
  task: string;
  project: string;
  total_time: string;
};

export function formatDateDisplay(isoDate: string) {
  // YYYY-MM-DD → DD/MM/YY
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate);
  if (!m) return isoDate;
  return `${m[3]}/${m[2]}/${m[1].slice(2)}`;
}

/** Parse H:MM or H into minutes. */
export function parseDurationToMinutes(raw: string): number | null {
  const text = raw.trim();
  if (!text) return null;
  if (text.includes(":")) {
    const [hPart, mPart] = text.split(":");
    const h = Number(hPart);
    const m = Number(mPart);
    if (!Number.isFinite(h) || !Number.isFinite(m) || h < 0 || m < 0 || m >= 60) {
      return null;
    }
    return h * 60 + m;
  }
  const hours = Number(text);
  if (!Number.isFinite(hours) || hours <= 0) return null;
  return Math.round(hours * 60);
}

export function minutesToHhMm(minutes: number) {
  const mins = Math.max(0, Math.round(minutes));
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return `${h}:${String(m).padStart(2, "0")}`;
}

export function sumSheetMinutes(rows: SheetRow[]) {
  return rows.reduce((sum, r) => {
    const mins = parseDurationToMinutes(r.total_time || "");
    return sum + (mins || 0);
  }, 0);
}

function escapeHtml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function newRowId(prefix = "row") {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

export function renumberSheetRows(
  rows: EditableSheetRow[],
  sheetDateIso: string
): EditableSheetRow[] {
  const dateStr = formatDateDisplay(sheetDateIso);
  let prevDate = "";
  return rows.map((r, i) => {
    const date = r.date || dateStr;
    const date_display = date !== prevDate ? date : "";
    prevDate = date;
    return {
      ...r,
      sr: i + 1,
      date,
      date_display,
    };
  });
}

export function baseRowsToEditable(
  rows: SheetRow[],
  sheetDateIso: string
): EditableSheetRow[] {
  const mapped = rows
    .filter((r) => !r.manual)
    .map((r, i) => ({
      ...r,
      id: `base-${i}-${r.in_time}-${r.out_time}-${r.total_time}-${r.task_summary || r.task}`,
      task_summary: r.task_summary || r.task,
      manual: false,
    }));
  return renumberSheetRows(mapped, sheetDateIso);
}

/** Keep manual rows at their previous indices after a Jira refresh.
 * Never mutates In/Out/Total of timer (non-manual) rows.
 */
export function mergeManualsIntoBase(
  baseRows: SheetRow[],
  previous: EditableSheetRow[],
  sheetDateIso: string
): EditableSheetRow[] {
  const manuals = previous
    .map((r, idx) => ({ row: r, idx }))
    .filter((x) => x.row.manual)
    .map(({ row, idx }) => ({
      idx,
      row: {
        ...row,
        // Manual rows never carry real clock times.
        in_time: "—",
        out_time: "—",
      },
    }));
  let next = baseRowsToEditable(baseRows, sheetDateIso);
  for (const { row, idx } of [...manuals].sort((a, b) => b.idx - a.idx)) {
    const pos = Math.max(0, Math.min(idx, next.length));
    next = [...next.slice(0, pos), row, ...next.slice(pos)];
  }
  return renumberSheetRows(next, sheetDateIso);
}

export function draftToManualRow(
  draft: ManualDraft,
  sheetDateIso: string
): EditableSheetRow | null {
  const task = draft.task.trim();
  const mins = parseDurationToMinutes(draft.total_time);
  if (!task || !mins) return null;
  const total_time = minutesToHhMm(mins);
  const date = formatDateDisplay(sheetDateIso);
  return {
    id: newRowId("manual"),
    sr: 0,
    date,
    date_display: date,
    // Never invent clock times — keep existing timer In/Out untouched.
    in_time: "—",
    out_time: "—",
    total_time,
    project: draft.project.trim() || "Other",
    task: `${task} - ${total_time}h`,
    task_summary: task,
    task_url: null,
    manual: true,
  };
}

export function insertEditableRow(
  rows: EditableSheetRow[],
  row: EditableSheetRow,
  atIndex: number,
  sheetDateIso: string
): EditableSheetRow[] {
  const pos = Math.max(0, Math.min(atIndex, rows.length));
  return renumberSheetRows(
    [...rows.slice(0, pos), row, ...rows.slice(pos)],
    sheetDateIso
  );
}

export function buildDailySheetHtml(opts: {
  rows: SheetRow[];
  totalTime: string;
  userName: string;
}) {
  const trHtml = opts.rows
    .map((r) => {
      const summary = escapeHtml(r.task_summary || r.task || "");
      const url = r.task_url
        ? `<br><a href="${escapeHtml(r.task_url)}">${escapeHtml(r.task_url)}</a>`
        : "";
      return `
        <tr>
          <td style="border:1px solid #999;padding:6px;text-align:center;">${escapeHtml(r.date_display || "")}</td>
          <td style="border:1px solid #999;padding:6px;text-align:center;">${escapeHtml(r.in_time || "")}</td>
          <td style="border:1px solid #999;padding:6px;text-align:center;">${escapeHtml(r.out_time || "")}</td>
          <td style="border:1px solid #999;padding:6px;text-align:center;">${escapeHtml(r.total_time || "")}</td>
          <td style="border:1px solid #999;padding:6px;text-align:center;">${escapeHtml(r.project || "")}</td>
          <td style="border:1px solid #999;padding:6px;white-space:pre-line;">
            ${summary}
            ${url}
            <span> - ${escapeHtml(r.total_time || "")}h</span>
          </td>
        </tr>`;
    })
    .join("");

  return `
    <div style="font-family:Arial,sans-serif;">
      <p><strong>GREETINGS:</strong><br/>Respected TL/PM/HR,</p>
      <table style="border-collapse:collapse;width:100%;font-size:13px;">
        <thead>
          <tr style="background:#f3c623;">
            <th style="border:1px solid #999;padding:6px;">Date</th>
            <th style="border:1px solid #999;padding:6px;">In-Time</th>
            <th style="border:1px solid #999;padding:6px;">Out-Time</th>
            <th style="border:1px solid #999;padding:6px;">Total Time</th>
            <th style="border:1px solid #999;padding:6px;">Project</th>
            <th style="border:1px solid #999;padding:6px;">Task</th>
          </tr>
        </thead>
        <tbody>
          ${trHtml}
        </tbody>
      </table>
      <p style="margin-top:12px;"><strong>Total:</strong> ${escapeHtml(opts.totalTime)}h</p>
      <p style="margin-top:16px;">Regards,<br/>${escapeHtml(opts.userName)}</p>
    </div>
  `;
}

export function buildDailySheetText(opts: {
  rows: SheetRow[];
  totalTime: string;
  userName: string;
}) {
  const lines = ["Date | In-Time | Out-Time | Total Time | Project | Task"];
  for (const r of opts.rows) {
    let taskText = r.task_summary || r.task || "";
    if (r.task_url) taskText += ` (${r.task_url})`;
    lines.push(
      `${r.date_display || ""} | ${r.in_time} | ${r.out_time} | ${r.total_time} | ${r.project} | ${taskText}`
    );
  }
  return (
    `GREETINGS:\n` +
    `Respected TL/PM/HR,\n\n` +
    `${lines.join("\n")}\n\n` +
    `Total: ${opts.totalTime}h\n\n` +
    `Regards,\n` +
    `${opts.userName}`
  );
}

/** Manual rows with insert positions for the backend (order-aware).
 * Do not send In/Out — manual rows must not override timer clock times.
 */
export function extrasPayload(rows: EditableSheetRow[]) {
  return rows
    .map((r, insert_at) => ({ r, insert_at }))
    .filter(({ r }) => r.manual && (r.task_summary || r.task)?.trim())
    .map(({ r, insert_at }) => ({
      task: (r.task_summary || r.task).trim(),
      project: (r.project || "").trim() || "Other",
      total_time: r.total_time,
      insert_at,
    }));
}
