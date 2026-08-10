"use client";

import { useMemo, useState } from "react";
import { JiraData, SharedComponent, normalizeComponents } from "../types";
import ComponentCodeModal from "./ComponentCodeModal";

type Props = {
  data: JiraData;
};

export default function SharedComponentsPanel({ data }: Props) {
  const [selected, setSelected] = useState<SharedComponent | null>(null);

  const components = useMemo(
    () => normalizeComponents(data.common_components),
    [data.common_components]
  );

  if (components.length === 0) return null;

  return (
    <>
      <div className="mt-10 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h3 className="text-lg font-bold text-slate-900">
              Shared components
            </h3>
            <p className="text-sm text-slate-500">
              Click a component to view and copy its generated code
            </p>
          </div>
          <span className="rounded-full bg-indigo-100 px-3 py-1 text-sm font-semibold text-indigo-800">
            {components.length}
          </span>
        </div>

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {components.map((component) => (
            <button
              key={component.name}
              type="button"
              onClick={() => setSelected(component)}
              className="group rounded-xl border border-slate-200 bg-slate-50 p-4 text-left transition hover:border-indigo-300 hover:bg-indigo-50/50 hover:shadow-md focus:outline-none focus:ring-2 focus:ring-indigo-400"
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="font-semibold text-slate-900 group-hover:text-indigo-700">
                    {component.name}
                  </p>
                  <p className="mt-1 font-mono text-xs text-slate-500 truncate">
                    {component.file_path}
                  </p>
                </div>
                <span className="shrink-0 rounded bg-slate-200 px-2 py-0.5 text-xs font-medium text-slate-600">
                  {component.language}
                </span>
              </div>
              <p className="mt-2 text-xs text-slate-600 line-clamp-2">
                {component.description}
              </p>
              <p className="mt-3 text-xs font-medium text-indigo-600 opacity-0 transition group-hover:opacity-100">
                View code →
              </p>
            </button>
          ))}
        </div>
      </div>

      <ComponentCodeModal
        component={selected}
        onClose={() => setSelected(null)}
      />
    </>
  );
}
