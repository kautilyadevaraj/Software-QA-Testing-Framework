"use client";

import { Activity, ClipboardCheck, FileText } from "lucide-react";

export type Phase3Tab = "testcases" | "execution" | "report";

const TABS: Array<{
  id: Phase3Tab;
  label: string;
  description: string;
  icon: typeof ClipboardCheck;
}> = [
  {
    id: "testcases",
    label: "Test Cases",
    description: "Review, edit, approve",
    icon: ClipboardCheck,
  },
  {
    id: "execution",
    label: "Live Execution",
    description: "Watch Playwright run",
    icon: Activity,
  },
  {
    id: "report",
    label: "Final Report",
    description: "Pass/fail summary",
    icon: FileText,
  },
];

export function Phase3TabBar({
  activeTab,
  onChange,
}: {
  activeTab: Phase3Tab;
  onChange: (tab: Phase3Tab) => void;
}) {
  return (
    <div className="grid gap-2 rounded-2xl border border-gray-200 bg-white p-2 shadow-sm sm:grid-cols-3">
      {TABS.map((tab) => {
        const Icon = tab.icon;
        const active = activeTab === tab.id;
        return (
          <button
            key={tab.id}
            type="button"
            onClick={() => onChange(tab.id)}
            className={`flex items-center gap-3 rounded-xl px-3 py-2.5 text-left transition ${
              active
                ? "bg-blue-50 text-blue-700 ring-1 ring-blue-100"
                : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
            }`}
          >
            <span
              className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${
                active ? "bg-blue-100" : "bg-gray-100"
              }`}
            >
              <Icon className="h-4 w-4" />
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-semibold">{tab.label}</span>
              <span className="block truncate text-xs opacity-75">{tab.description}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}
