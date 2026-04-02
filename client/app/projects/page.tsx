"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowDown, ArrowUp, ChevronDown, ChevronLeft, ChevronRight, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  ProjectRecord,
  createEmptyDocuments,
  getDefaultProjects,
  loadProjectsFromStorage,
  saveProjectsToStorage,
} from "@/lib/projects";
import { cn } from "@/lib/utils";

type SortField = "id" | "name" | "createdAt" | "version" | "status";
type SortDirection = "asc" | "desc";
type PageSizeOption = 20 | 50 | "all";

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

type AddProjectForm = {
  name: string;
  description: string;
  teamMembers: string;
};

const emptyAddForm: AddProjectForm = {
  name: "",
  description: "",
  teamMembers: "",
};

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    timeZone: "UTC",
  }).format(new Date(value));
}

function parseTeamMembers(raw: string) {
  return raw
    .split(";")
    .map((member) => member.trim())
    .filter(Boolean);
}

function generateProjectId(existing: ProjectRecord[]) {
  let candidate = `PRJ-${Math.floor(1000 + Math.random() * 9000)}`;
  const existingIds = new Set(existing.map((project) => project.id));

  while (existingIds.has(candidate)) {
    candidate = `PRJ-${Math.floor(1000 + Math.random() * 9000)}`;
  }

  return candidate;
}

export default function ProjectsPage() {
  const router = useRouter();

  const [projects, setProjects] = useState<ProjectRecord[]>(() => getDefaultProjects());
  const hasMountedRef = useRef(false);

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      setProjects(loadProjectsFromStorage());
    });

    return () => cancelAnimationFrame(frame);
  }, []);

  // Default: newest projects first based on created date.
  const [sortField, setSortField] = useState<SortField>("createdAt");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const [pageSize, setPageSize] = useState<PageSizeOption>(20);
  const [currentPage, setCurrentPage] = useState(1);

  const [expandedProjectIds, setExpandedProjectIds] = useState<Record<string, boolean>>({});

  const [isAddDialogOpen, setIsAddDialogOpen] = useState(false);
  const [addForm, setAddForm] = useState<AddProjectForm>(emptyAddForm);

  const [projectPendingDelete, setProjectPendingDelete] = useState<ProjectRecord | null>(null);
  const [deleteConfirmText, setDeleteConfirmText] = useState("");

  useEffect(() => {
    if (!hasMountedRef.current) {
      hasMountedRef.current = true;
      return;
    }

    saveProjectsToStorage(projects);
  }, [projects]);

  const sortedProjects = useMemo(() => {
    const list = [...projects];

    list.sort((a, b) => {
      let comparison = 0;

      if (sortField === "createdAt") {
        comparison = new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime();
      } else {
        comparison = a[sortField].localeCompare(b[sortField]);
      }

      return sortDirection === "asc" ? comparison : -comparison;
    });

    return list;
  }, [projects, sortDirection, sortField]);

  const effectivePageSize = pageSize === "all" ? Math.max(1, sortedProjects.length) : pageSize;
  const totalPages = Math.max(1, Math.ceil(sortedProjects.length / effectivePageSize));
  const safeCurrentPage = Math.min(currentPage, totalPages);

  const paginatedProjects = useMemo(() => {
    const start = (safeCurrentPage - 1) * effectivePageSize;
    const end = start + effectivePageSize;
    return sortedProjects.slice(start, end);
  }, [effectivePageSize, safeCurrentPage, sortedProjects]);

  const handleSort = useCallback(
    (field: SortField) => {
      if (sortField === field) {
        setSortDirection((direction) => (direction === "asc" ? "desc" : "asc"));
      } else {
        setSortField(field);
        setSortDirection("asc");
      }

      setCurrentPage(1);
    },
    [sortField]
  );

  const getSortIndicator = useCallback(
    (field: SortField) => {
      const isActive = sortField === field;

      return (
        <span className="inline-flex flex-col leading-none">
          <ArrowUp
            className={cn(
              "h-3 w-3",
              isActive && sortDirection === "asc" ? "text-[#2a63f5]" : "text-black/35"
            )}
          />
          <ArrowDown
            className={cn(
              "-mt-0.5 h-3 w-3",
              isActive && sortDirection === "desc" ? "text-[#2a63f5]" : "text-black/35"
            )}
          />
        </span>
      );
    },
    [sortDirection, sortField]
  );

  const handleAddProject = useCallback(() => {
    const trimmedName = addForm.name.trim();
    const members = parseTeamMembers(addForm.teamMembers);

    if (!trimmedName) {
      toast.error("Project Name is required.");
      return;
    }

    if (members.length === 0) {
      toast.error("At least one testing team member email is required.");
      return;
    }

    const invalidEmail = members.find((member) => !EMAIL_REGEX.test(member));
    if (invalidEmail) {
      toast.error(`Invalid email in testing team members: ${invalidEmail}`);
      return;
    }

    setProjects((currentProjects) => {
      const newProject: ProjectRecord = {
        id: generateProjectId(currentProjects),
        name: trimmedName,
        description: addForm.description.trim(),
        createdAt: new Date().toISOString(),
        version: "v1.0.0",
        status: "Draft",
        testers: members,
        url: "",
        documents: createEmptyDocuments(),
      };

      return [newProject, ...currentProjects];
    });

    setAddForm(emptyAddForm);
    setIsAddDialogOpen(false);
    setCurrentPage(1);
    toast.success("Project created.");
  }, [addForm]);

  const handleDeleteProject = useCallback(() => {
    if (!projectPendingDelete) {
      return;
    }

    if (deleteConfirmText.trim() !== projectPendingDelete.name) {
      toast.error("Enter the exact project name to confirm deletion.");
      return;
    }

    setProjects((currentProjects) => currentProjects.filter((project) => project.id !== projectPendingDelete.id));
    setExpandedProjectIds((current) => {
      const next = { ...current };
      delete next[projectPendingDelete.id];
      return next;
    });
    setProjectPendingDelete(null);
    setDeleteConfirmText("");
    toast.success("Project deleted.");
  }, [deleteConfirmText, projectPendingDelete]);

  return (
    <Card className="h-full rounded-none border-black/10 bg-white shadow-sm">
      <CardHeader className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <CardTitle className="text-2xl text-black">Projects</CardTitle>
          <CardDescription>Manage active QA projects, assignments, and status snapshots.</CardDescription>
        </div>

        <div className="flex flex-wrap items-center gap-2 sm:self-start">
          <label htmlFor="page-size" className="text-sm text-black/70">
            Show
          </label>
          <select
            id="page-size"
            value={pageSize}
            onChange={(event) => {
              const selected = event.target.value;
              setPageSize(selected === "all" ? "all" : Number(selected) as PageSizeOption);
              setCurrentPage(1);
            }}
            className="h-9 rounded-md border border-black/20 bg-white px-2 text-sm text-black focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2a63f5]"
          >
            <option value={20}>20</option>
            <option value={50}>50</option>
            <option value="all">All</option>
          </select>

          <Button
            variant="outline"
            size="sm"
            onClick={() => setCurrentPage((page) => Math.max(1, Math.min(page, totalPages) - 1))}
            disabled={safeCurrentPage === 1}
          >
            <ChevronLeft className="h-4 w-4" />
            Previous
          </Button>
          <span className="px-1 text-sm text-black/70">
            Page {safeCurrentPage} / {totalPages}
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setCurrentPage((page) => Math.min(totalPages, Math.min(page, totalPages) + 1))}
            disabled={safeCurrentPage >= totalPages}
          >
            Next
            <ChevronRight className="h-4 w-4" />
          </Button>

          <Button onClick={() => setIsAddDialogOpen(true)} className="ml-1">
            <Plus className="h-4 w-4" />
            Add Project
          </Button>
        </div>
      </CardHeader>

      <CardContent>
        <div className="overflow-x-auto rounded-none border border-black/10">
          <table className="min-w-full border-collapse text-sm">
            <thead>
              <tr className="bg-[#2a63f5]/10 text-left text-black">
                <th className="px-3 py-3 font-semibold">Sl.No</th>
                <th className="px-3 py-3 font-semibold">
                  <button
                    type="button"
                    onClick={() => handleSort("id")}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-md px-2 py-1 transition-colors",
                      sortField === "id" ? "bg-white text-[#2a63f5]" : "text-black/80 hover:bg-white/70"
                    )}
                  >
                    Project ID
                    {getSortIndicator("id")}
                  </button>
                </th>
                <th className="px-3 py-3 font-semibold">
                  <button
                    type="button"
                    onClick={() => handleSort("name")}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-md px-2 py-1 transition-colors",
                      sortField === "name" ? "bg-white text-[#2a63f5]" : "text-black/80 hover:bg-white/70"
                    )}
                  >
                    Project Name
                    {getSortIndicator("name")}
                  </button>
                </th>
                <th className="px-3 py-3 font-semibold">
                  <button
                    type="button"
                    onClick={() => handleSort("createdAt")}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-md px-2 py-1 transition-colors",
                      sortField === "createdAt" ? "bg-white text-[#2a63f5]" : "text-black/80 hover:bg-white/70"
                    )}
                  >
                    Created Date
                    {getSortIndicator("createdAt")}
                  </button>
                </th>
                <th className="px-3 py-3 font-semibold">
                  <button
                    type="button"
                    onClick={() => handleSort("version")}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-md px-2 py-1 transition-colors",
                      sortField === "version" ? "bg-white text-[#2a63f5]" : "text-black/80 hover:bg-white/70"
                    )}
                  >
                    Version
                    {getSortIndicator("version")}
                  </button>
                </th>
                <th className="px-3 py-3 font-semibold">
                  <button
                    type="button"
                    onClick={() => handleSort("status")}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-md px-2 py-1 transition-colors",
                      sortField === "status" ? "bg-white text-[#2a63f5]" : "text-black/80 hover:bg-white/70"
                    )}
                  >
                    Status
                    {getSortIndicator("status")}
                  </button>
                </th>
                <th className="px-3 py-3 font-semibold">Delete</th>
                <th className="px-3 py-3 font-semibold text-center">
                  <span className="sr-only">Expand Row</span>
                </th>
              </tr>
            </thead>

            <tbody>
              {paginatedProjects.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-3 py-8 text-center text-black/60">
                    No projects available.
                  </td>
                </tr>
              ) : (
                paginatedProjects.map((project, index) => {
                  const serialNumber = (safeCurrentPage - 1) * effectivePageSize + index + 1;
                  const isExpanded = Boolean(expandedProjectIds[project.id]);

                  return (
                    <FragmentRow
                      key={project.id}
                      project={project}
                      serialNumber={serialNumber}
                      isExpanded={isExpanded}
                      onOpenProject={() => router.push(`/projects/${project.id}`)}
                      onToggleExpand={() =>
                        setExpandedProjectIds((current) => ({
                          ...current,
                          [project.id]: !current[project.id],
                        }))
                      }
                      onRequestDelete={() => {
                        setProjectPendingDelete(project);
                        setDeleteConfirmText("");
                      }}
                    />
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <p className="text-sm text-black/65">
            {sortedProjects.length > 0
              ? `Showing ${(safeCurrentPage - 1) * effectivePageSize + 1} to ${Math.min(
                  safeCurrentPage * effectivePageSize,
                  sortedProjects.length
                )} of ${sortedProjects.length}`
              : "Showing 0 of 0"}
          </p>
        </div>
      </CardContent>

      {isAddDialogOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <div className="w-full max-w-xl rounded-xl border border-black/15 bg-white p-6 shadow-xl">
            <h2 className="text-xl font-semibold text-black">Add Project</h2>
            <p className="mt-1 text-sm text-black/65">Fill required fields now. Project URL and documents can be updated in Project Configuration.</p>

            <div className="mt-5 space-y-4">
              <div className="space-y-2">
                <Label htmlFor="new-project-name">Project Name *</Label>
                <Input
                  id="new-project-name"
                  value={addForm.name}
                  onChange={(event) => setAddForm((current) => ({ ...current, name: event.target.value }))}
                  placeholder="Enter project name"
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="new-project-description">Project Description</Label>
                <textarea
                  id="new-project-description"
                  rows={3}
                  value={addForm.description}
                  onChange={(event) => setAddForm((current) => ({ ...current, description: event.target.value }))}
                  className="flex w-full resize-none rounded-md border border-black/20 bg-white px-3 py-2 text-sm text-black placeholder:text-black/45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2a63f5]"
                  placeholder="Enter a short description"
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="new-project-team">Testing Team Members Email IDs * (semicolon separated)</Label>
                <Input
                  id="new-project-team"
                  value={addForm.teamMembers}
                  onChange={(event) => setAddForm((current) => ({ ...current, teamMembers: event.target.value }))}
                  placeholder="tester1@company.com;tester2@company.com"
                />
              </div>

            </div>

            <div className="mt-6 flex justify-end gap-2">
              <Button
                variant="outline"
                onClick={() => {
                  setIsAddDialogOpen(false);
                  setAddForm(emptyAddForm);
                }}
              >
                Cancel
              </Button>
              <Button onClick={handleAddProject}>Create Project</Button>
            </div>
          </div>
        </div>
      ) : null}

      {projectPendingDelete ? (
        <div className="fixed inset-0 z-60 flex items-center justify-center bg-black/35 p-4">
          <div className="w-full max-w-lg rounded-xl border border-black/15 bg-white p-6 shadow-xl">
            <h2 className="text-xl font-semibold text-black">Delete Project</h2>
            <p className="mt-2 text-sm text-black/70">
              This action cannot be undone. To confirm deletion, enter the exact project name:
            </p>
            <p className="mt-2 rounded-md bg-[#2a63f5]/10 px-3 py-2 text-sm font-medium text-[#2a63f5]">
              {projectPendingDelete.name}
            </p>

            <div className="mt-4 space-y-2">
              <Label htmlFor="delete-project-confirm">Project Name</Label>
              <Input
                id="delete-project-confirm"
                value={deleteConfirmText}
                onChange={(event) => setDeleteConfirmText(event.target.value)}
                placeholder="Type project name to confirm"
              />
            </div>

            <div className="mt-6 flex justify-end gap-2">
              <Button
                variant="outline"
                onClick={() => {
                  setProjectPendingDelete(null);
                  setDeleteConfirmText("");
                }}
              >
                Cancel
              </Button>
              <Button
                onClick={handleDeleteProject}
                disabled={deleteConfirmText.trim() !== projectPendingDelete.name}
                className="bg-red-600 text-white hover:bg-red-700 disabled:bg-red-400"
              >
                <Trash2 className="h-4 w-4" />
                Delete Project
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </Card>
  );
}

type FragmentRowProps = {
  project: ProjectRecord;
  serialNumber: number;
  isExpanded: boolean;
  onOpenProject: () => void;
  onToggleExpand: () => void;
  onRequestDelete: () => void;
};

function FragmentRow({ project, serialNumber, isExpanded, onOpenProject, onToggleExpand, onRequestDelete }: FragmentRowProps) {
  return (
    <>
      <tr className="cursor-pointer border-t border-black/10 hover:bg-[#2a63f5]/5" onClick={onOpenProject}>
        <td className="px-3 py-3 text-black/85">{serialNumber}</td>
        <td className="px-3 py-3 font-medium text-black">{project.id}</td>
        <td className="px-3 py-3 text-black">{project.name}</td>
        <td className="px-3 py-3 text-black/70">{formatDate(project.createdAt)}</td>
        <td className="px-3 py-3 text-black/70">{project.version}</td>
        <td className="px-3 py-3">
          <span
            className={cn(
              "inline-flex rounded-full px-2.5 py-1 text-xs font-medium",
              project.status === "Active"
                ? "bg-[#2a63f5]/10 text-[#2a63f5]"
                : project.status === "Blocked"
                  ? "bg-red-100 text-red-700"
                  : "bg-black/10 text-black/70"
            )}
          >
            {project.status}
          </span>
        </td>
        <td className="px-3 py-3" onClick={(event) => event.stopPropagation()}>
          <Button
            variant="outline"
            size="sm"
            onClick={onRequestDelete}
            className="border-red-300 text-red-600 hover:bg-red-50 hover:text-red-700"
          >
            <Trash2 className="h-4 w-4" />
            Delete
          </Button>
        </td>
        <td className="px-3 py-3" onClick={(event) => event.stopPropagation()}>
          <Button variant="outline" size="icon" onClick={onToggleExpand} aria-label="Toggle project details">
            <ChevronDown className={cn("h-4 w-4 transition-transform", isExpanded ? "rotate-180" : "rotate-0")} />
          </Button>
        </td>
      </tr>

      {isExpanded ? (
        <tr className="border-t border-black/10 bg-[#2a63f5]/5">
          <td colSpan={8} className="px-4 py-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-black/60">Description</p>
                <p className="mt-1 text-sm text-black/80">{project.description || "No description provided."}</p>
              </div>

              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-black/60">Testers Working On Project</p>
                <p className="mt-1 text-sm text-black/80">{project.testers.join(", ")}</p>
              </div>
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}
