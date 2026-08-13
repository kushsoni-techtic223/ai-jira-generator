"use client";

import { saveEmailRecipients } from "../lib/emailRecipients";

export default function EmailRecipientFields({
  to,
  cc,
  onChange,
  className = "",
}: {
  to: string;
  cc: string;
  onChange: (next: { to: string; cc: string }) => void;
  className?: string;
}) {
  const update = (next: { to: string; cc: string }) => {
    onChange(next);
    saveEmailRecipients(next);
  };

  return (
    <div className={`grid gap-3 sm:grid-cols-2 ${className}`}>
      <label className="text-sm text-slate-700 sm:col-span-2">
        To
        <input
          type="text"
          value={to}
          onChange={(e) => update({ to: e.target.value, cc })}
          placeholder="devansh@techtic.agency, brijen@techtic.agency"
          autoComplete="off"
          spellCheck={false}
          className="relative z-10 mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none ring-0 focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
        />
        <span className="mt-1 block text-xs text-slate-500">
          Comma-separated. Saved locally in this browser.
        </span>
      </label>
      <label className="text-sm text-slate-700 sm:col-span-2">
        CC
        <input
          type="text"
          value={cc}
          onChange={(e) => update({ to, cc: e.target.value })}
          placeholder="optional@company.com"
          autoComplete="off"
          spellCheck={false}
          className="relative z-10 mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none ring-0 focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
        />
      </label>
    </div>
  );
}
