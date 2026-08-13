"use client";

import { FormEvent, useEffect, useState } from "react";
import { formatDuration, PendingWorklog } from "../hooks/useWorkTimer";

type Props = {
  pending: PendingWorklog;
  logging: boolean;
  error?: string | null;
  onCancel: () => void;
  onSave: (description: string) => void | Promise<unknown>;
};

export default function WorklogDescriptionModal({
  pending,
  logging,
  error,
  onCancel,
  onSave
}: Props) {
  const [description, setDescription] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  useEffect(() => {
    setDescription("");
    setLocalError(null);
  }, [pending.issueKey, pending.endedAt]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const text = description.trim();
    if (!text) {
      setLocalError("Enter a short description of what you worked on.");
      return;
    }
    setLocalError(null);
    await onSave(text);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      onClick={() => {
        if (!logging) onCancel();
      }}
    >
      <div className="absolute inset-0 bg-slate-900/60 backdrop-blur-sm" />

      <form
        className="relative z-10 w-full max-w-md overflow-hidden rounded-2xl bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        onSubmit={submit}
      >
        <div className="border-b border-slate-200 bg-slate-50 px-5 py-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-teal-700">
            Log work · {pending.issueKey}
          </p>
          <h2 className="mt-1 text-lg font-bold text-slate-900">
            What did you work on?
          </h2>
          <p className="mt-1 truncate text-sm text-slate-600">{pending.title}</p>
          <p className="mt-2 font-mono text-sm font-semibold tabular-nums text-teal-800">
            Time: {formatDuration(pending.seconds)}
          </p>
        </div>

        <div className="px-5 py-4">
          <label
            htmlFor="worklog-description"
            className="mb-1.5 block text-sm font-medium text-slate-700"
          >
            Log description
          </label>
          <textarea
            id="worklog-description"
            autoFocus
            rows={4}
            value={description}
            disabled={logging}
            onChange={(e) => {
              setDescription(e.target.value);
              if (localError) setLocalError(null);
            }}
            placeholder="e.g. Implemented login API validation and unit tests"
            className="w-full resize-y rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 outline-none ring-teal-600 placeholder:text-slate-400 focus:border-teal-500 focus:ring-2"
          />
          <p className="mt-1.5 text-xs text-slate-500">
            This text is saved as the Jira worklog comment.
          </p>
          {(localError || error) && (
            <p className="mt-2 text-sm text-red-700">{localError || error}</p>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-slate-200 bg-slate-50 px-5 py-3">
          <button
            type="button"
            disabled={logging}
            onClick={onCancel}
            className="rounded-lg px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-200 disabled:opacity-60"
          >
            Resume timer
          </button>
          <button
            type="submit"
            disabled={logging || !description.trim()}
            className="rounded-lg bg-teal-800 px-3 py-1.5 text-sm font-semibold text-white hover:bg-teal-900 disabled:opacity-60"
          >
            {logging ? "Saving to Jira…" : "Save log"}
          </button>
        </div>
      </form>
    </div>
  );
}
