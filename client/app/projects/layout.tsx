"use client";

import type { ReactNode } from "react";
import { useState } from "react";
import { usePathname } from "next/navigation";
import { ProjectsSideNav } from "@/components/projects-side-nav";
import { cn } from "@/lib/utils";

export default function ProjectsLayout({ children }: { children: ReactNode }) {
  const [isSidebarExpanded, setIsSidebarExpanded] = useState(false);
  const pathname = usePathname();
  const isProjectsListPage = pathname === "/projects";

  return (
    <div className="h-full w-full overflow-hidden">
      <ProjectsSideNav isExpanded={isSidebarExpanded} onExpandedChange={setIsSidebarExpanded} />
      <section
        className={cn(
          "h-full w-full min-w-0 overflow-x-hidden pr-0 transition-[padding] duration-200 ease-out",
          isProjectsListPage ? "overflow-y-hidden" : "overflow-y-auto",
          isSidebarExpanded ? "pl-56" : "pl-16"
        )}
      >
        {children}
      </section>
    </div>
  );
}
