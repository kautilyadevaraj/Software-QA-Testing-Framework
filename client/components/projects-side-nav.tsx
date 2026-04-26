"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { FileText } from "lucide-react";
import { cn } from "@/lib/utils";

const navItems = [
  {
    href: "/projects",
    label: "Projects",
    icon: FileText,
  },
];

type ProjectsSideNavProps = {
  isExpanded: boolean;
  onExpandedChange: (value: boolean) => void;
};

export function ProjectsSideNav({ isExpanded, onExpandedChange }: ProjectsSideNavProps) {
  const pathname = usePathname();

  return (
    <aside
      className={cn(
        "fixed left-0 top-16 z-40 h-[calc(100dvh-4rem)] shrink-0 overflow-hidden border-r border-black/10 bg-white shadow-sm transition-[width] duration-200 ease-out",
        isExpanded ? "w-56" : "w-16"
      )}
      onMouseEnter={() => onExpandedChange(true)}
      onMouseLeave={() => onExpandedChange(false)}
    >
      <div className="p-2">
        <nav className="space-y-1">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = pathname === item.href || pathname.startsWith(`${item.href}/`);

            return (
              <Link
                key={`${item.href}-${item.label}`}
                href={item.href}
                onClick={() => onExpandedChange(false)}
                className={cn(
                  "flex h-11 items-center gap-3 rounded-md px-3 text-sm font-medium transition-colors",
                  isActive ? "bg-[#2a63f5]/12 text-[#2a63f5]" : "text-black/70 hover:bg-[#2a63f5]/10 hover:text-black"
                )}
              >
                <Icon className="h-5 w-5 shrink-0" />
                <span
                  className={cn(
                    "overflow-hidden whitespace-nowrap transition-all duration-200",
                    isExpanded ? "w-auto opacity-100" : "w-0 opacity-0"
                  )}
                >
                  {item.label}
                </span>
              </Link>
            );
          })}
        </nav>
      </div>
    </aside>
  );
}
