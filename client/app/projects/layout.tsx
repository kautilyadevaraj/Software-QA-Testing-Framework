"use client";

import type { ReactNode } from "react";
import { useState } from "react";
import { ProjectsSideNav } from "@/components/projects-side-nav";
import { cn } from "@/lib/utils";

export default function ProjectsLayout({ children }: { children: ReactNode }) {
  const [isSidebarExpanded, setIsSidebarExpanded] = useState(false);

  return (
    <div className="min-h-[calc(100vh-4rem)] w-full">
      <ProjectsSideNav isExpanded={isSidebarExpanded} onExpandedChange={setIsSidebarExpanded} />
      <section
        className={cn(
          "min-h-[calc(100vh-4rem)] w-full min-w-0 overflow-y-auto pr-0 transition-[padding] duration-200 ease-out",
          isSidebarExpanded ? "pl-56" : "pl-16"
        )}
      >
        {children}
      </section>
    </div>
  );
}
