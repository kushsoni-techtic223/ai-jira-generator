"use client";

import { useState } from "react";
import { SharedComponent } from "../types";

type Props = {
  component: SharedComponent | null;
  onClose: () => void;
};

export default function ComponentCodeModal({ component, onClose }: Props) {
  const [copied, setCopied] = useState(false);

  if (!component) return null;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(component.code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div className="absolute inset-0 bg-slate-900/60 backdrop-blur-sm" />

      <div
        className="relative z-10 flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b border-slate-200 bg-slate-900 px-6 py-4 text-white">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-indigo-300">
                Shared component
              </p>
              <h2 className="mt-1 text-xl font-bold">{component.name}</h2>
              <p className="mt-1 font-mono text-xs text-slate-400">
                {component.file_path}
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg p-2 text-slate-400 hover:bg-slate-800 hover:text-white"
              aria-label="Close"
            >
              ✕
            </button>
          </div>
        </div>

        <div className="border-b border-slate-200 bg-slate-50 px-6 py-3">
          <p className="text-sm text-slate-600">{component.description}</p>
          <div className="mt-2 flex gap-2">
            <span className="rounded-md bg-slate-200 px-2 py-0.5 text-xs font-medium text-slate-700">
              {component.language}
            </span>
          </div>
        </div>

        <div className="flex items-center justify-between border-b border-slate-200 bg-slate-100 px-4 py-2">
          <span className="text-xs font-semibold uppercase text-slate-500">
            Source code
          </span>
          <button
            type="button"
            onClick={handleCopy}
            className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700"
          >
            {copied ? "Copied!" : "Copy code"}
          </button>
        </div>

        <pre className="flex-1 overflow-auto bg-slate-950 p-4 text-sm leading-relaxed text-emerald-300">
          <code>{component.code}</code>
        </pre>

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
