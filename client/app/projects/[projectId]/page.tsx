"use client";

import Link from "next/link";
import { ChangeEvent, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { ArrowLeft, ArrowRight, FileText, Play, Save, TestTubeDiagonal, Ticket, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  DOCUMENT_CATEGORIES,
  ProjectDocuments,
  ProjectRecord,
  REQUIRED_DOCUMENT_CATEGORIES,
  getDefaultProjects,
  loadProjectsFromStorage,
  saveProjectsToStorage,
} from "@/lib/projects";
import { cn } from "@/lib/utils";

type ActiveTab = "qa" | "configuration";

type ProjectFormState = {
  name: string;
  description: string;
  teamMembers: string;
  url: string;
  documents: ProjectDocuments;
};

const MAX_DOCUMENT_SIZE_MB = 20;
const MAX_DOCUMENT_SIZE_BYTES = MAX_DOCUMENT_SIZE_MB * 1024 * 1024;

function toFormState(project: ProjectRecord): ProjectFormState {
  return {
    name: project.name,
    description: project.description,
    teamMembers: project.testers.join(";"),
    url: project.url,
    documents: {
      BRD: [...project.documents.BRD],
      FSD: [...project.documents.FSD],
      WBS: [...project.documents.WBS],
      SwaggerDocs: [...project.documents.SwaggerDocs],
      Credentials: [...project.documents.Credentials],
      Assumptions: [...project.documents.Assumptions],
    },
  };
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    timeZone: "UTC",
  }).format(new Date(value));
}

function formatCategoryLabel(category: keyof ProjectDocuments) {
  const baseLabel = category === "SwaggerDocs" ? "Swagger Docs" : category;
  const isRequired = REQUIRED_DOCUMENT_CATEGORIES.includes(category);
  return `${baseLabel}${isRequired ? "*" : ""}`;
}

function isPdfFile(file: File) {
  const isPdfByType = file.type === "application/pdf";
  const isPdfByName = file.name.toLowerCase().endsWith(".pdf");
  return isPdfByType || isPdfByName;
}

function isSwaggerFile(file: File) {
  const lower = file.name.toLowerCase();
  const isYamlByName = lower.endsWith(".yaml") || lower.endsWith(".yml");
  const isJsonByName = lower.endsWith(".json");
  const isYamlByType = file.type === "application/yaml" || file.type === "text/yaml";
  const isJsonByType = file.type === "application/json";
  return isYamlByName || isJsonByName || isYamlByType || isJsonByType;
}

function getAcceptedFormatLabel(category: keyof ProjectDocuments) {
  return category === "SwaggerDocs" ? "YAML or JSON" : "PDF";
}

function cloneDocumentsMap(documents: ProjectDocuments): ProjectDocuments {
  return {
    BRD: [...documents.BRD],
    FSD: [...documents.FSD],
    WBS: [...documents.WBS],
    SwaggerDocs: [...documents.SwaggerDocs],
    Credentials: [...documents.Credentials],
    Assumptions: [...documents.Assumptions],
  };
}

export default function ProjectDetailsPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = params.projectId;

  const initialProjects = useMemo(() => getDefaultProjects(), []);
  const [projects, setProjects] = useState<ProjectRecord[]>(initialProjects);
  const [activeTab, setActiveTab] = useState<ActiveTab>("configuration");
  const [isLaunched, setIsLaunched] = useState(false);
  const [isProceedConfirmed, setIsProceedConfirmed] = useState(false);
  const [formState, setFormState] = useState<ProjectFormState | null>(() => {
    const initialProject = initialProjects.find((item) => item.id === projectId);
    return initialProject ? toFormState(initialProject) : null;
  });

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const storedProjects = loadProjectsFromStorage();
      setProjects(storedProjects);

      const storedProject = storedProjects.find((item) => item.id === projectId);
      setFormState(storedProject ? toFormState(storedProject) : null);
      setIsLaunched(false);
      setIsProceedConfirmed(false);
    });

    return () => cancelAnimationFrame(frame);
  }, [projectId]);

  const project = useMemo(() => projects.find((item) => item.id === projectId), [projectId, projects]);
  const canIngestAndAddDocuments = isLaunched && isProceedConfirmed;

  const handleSave = () => {
    if (!project || !formState) {
      return;
    }

    const testers = formState.teamMembers
      .split(";")
      .map((member) => member.trim())
      .filter(Boolean);

    if (!formState.name.trim()) {
      toast.error("Project name is required.");
      return;
    }

    if (testers.length === 0) {
      toast.error("Add at least one testing team member email.");
      return;
    }

    for (const requiredCategory of REQUIRED_DOCUMENT_CATEGORIES) {
      if (formState.documents[requiredCategory].length === 0) {
        toast.error(`${formatCategoryLabel(requiredCategory)} requires at least one ${getAcceptedFormatLabel(requiredCategory)} file.`);
        return;
      }
    }

    const updatedProjects = projects.map((item) => {
      if (item.id !== project.id) {
        return item;
      }

      return {
        ...item,
        name: formState.name.trim(),
        description: formState.description.trim(),
        testers,
        url: formState.url.trim(),
        documents: cloneDocumentsMap(formState.documents),
      };
    });

    setProjects(updatedProjects);
    saveProjectsToStorage(updatedProjects);
    toast.success("Project configuration updated.");
  };

  const handleDocumentUpload = (category: keyof ProjectDocuments, event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    if (!files.length) {
      return;
    }

    const accepted: string[] = [];

    for (const file of files) {
      const isAllowed = category === "SwaggerDocs" ? isSwaggerFile(file) : isPdfFile(file);
      if (!isAllowed) {
        toast.error(`${file.name} is not a valid ${getAcceptedFormatLabel(category)} file.`);
        continue;
      }

      if (file.size > MAX_DOCUMENT_SIZE_BYTES) {
        toast.error(`${file.name} exceeds ${MAX_DOCUMENT_SIZE_MB}MB.`);
        continue;
      }

      accepted.push(file.name);
    }

    if (accepted.length === 0) {
      event.target.value = "";
      return;
    }

    setFormState((current) => {
      if (!current) {
        return current;
      }

      const existing = current.documents[category];
      const merged = [...existing, ...accepted.filter((name) => !existing.includes(name))];

      return {
        ...current,
        documents: {
          ...current.documents,
          [category]: merged,
        },
      };
    });

    event.target.value = "";
  };

  const handleLaunch = () => {
    if (!formState?.url.trim()) {
      toast.error("Project URL is required to launch.");
      return;
    }

    setIsLaunched(true);
    setIsProceedConfirmed(false);
    toast.success("Project URL launched.");
  };

  const handleIngest = () => {
    if (!canIngestAndAddDocuments) {
      return;
    }

    toast.success("Ingestion started.");
  };

  const removeDocument = (category: keyof ProjectDocuments, documentName: string, index: number) => {
    setFormState((current) => {
      if (!current) {
        return current;
      }

      const nextDocuments = { ...current.documents };
      nextDocuments[category] = current.documents[category].filter(
        (name, itemIndex) => !(name === documentName && itemIndex === index)
      );

      return {
        ...current,
        documents: nextDocuments,
      };
    });
  };

  if (!project || !formState) {
    return (
      <Card className="border-black/10 bg-white">
        <CardHeader>
          <CardTitle className="text-black">Project not found</CardTitle>
          <CardDescription>The requested project does not exist or was removed.</CardDescription>
        </CardHeader>
        <CardContent>
          <Button asChild>
            <Link href="/projects">Back to Projects</Link>
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4 px-4 py-4 sm:px-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Button asChild variant="ghost" className="-ml-3">
            <Link href="/projects">
              <ArrowLeft className="h-4 w-4" />
              Back to Projects
            </Link>
          </Button>
          <h1 className="text-2xl font-semibold text-black">{project.name}</h1>
          <p className="text-sm text-black/70">
            {project.id} | Created {formatDate(project.createdAt)} | {project.version}
          </p>
        </div>

        <div className="flex flex-col items-start gap-2 sm:items-end">
          <div className="rounded-lg border border-black/10 bg-white px-3 py-2 text-sm text-black/70">
            Status:{" "}
            <span className={cn("font-semibold", project.status === "Active" ? "text-[#2a63f5]" : "text-black")}>
              {project.status}
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button variant={activeTab === "qa" ? "default" : "outline"} onClick={() => setActiveTab("qa")}>
              QA Testing
            </Button>
            <Button
              variant={activeTab === "configuration" ? "default" : "outline"}
              onClick={() => setActiveTab("configuration")}
            >
              Project Configuration
            </Button>
          </div>
        </div>
      </div>

      {activeTab === "qa" ? (
        <Card className="border-black/10 bg-white">
          <CardHeader>
            <CardTitle className="text-black">QA Testing</CardTitle>
            <CardDescription>Execution console will be available here.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <div className="rounded-lg border border-black/10 bg-[#2a63f5]/5 p-4">
                <p className="text-xs uppercase tracking-wide text-black/60">Suite Status</p>
                <p className="mt-2 text-lg font-semibold text-black">Pending</p>
              </div>
              <div className="rounded-lg border border-black/10 bg-[#2a63f5]/5 p-4">
                <p className="text-xs uppercase tracking-wide text-black/60">Total Cases</p>
                <p className="mt-2 text-lg font-semibold text-black">0</p>
              </div>
              <div className="rounded-lg border border-black/10 bg-[#2a63f5]/5 p-4">
                <p className="text-xs uppercase tracking-wide text-black/60">Last Run</p>
                <p className="mt-2 text-lg font-semibold text-black">Not Started</p>
              </div>
            </div>

            <div className="rounded-lg border border-dashed border-black/20 bg-[#2a63f5]/5 p-10 text-center text-sm text-black/60">
              <div className="flex flex-col items-center gap-2">
                <TestTubeDiagonal className="h-6 w-6 text-[#2a63f5]" />
                QA Testing content will be added in upcoming iterations.
              </div>
            </div>
          </CardContent>
        </Card>
      ) : (
        <Card className="border-black/10 bg-white">
          <CardHeader>
            <CardTitle className="text-black">Project Configuration</CardTitle>
            <CardDescription>Edit name, description, testing team, and documents.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="grid grid-cols-1 gap-4">
              <div className="space-y-2">
                <Label htmlFor="project-name">Project Name</Label>
                <Input
                  id="project-name"
                  value={formState.name}
                  onChange={(event) => setFormState((current) => (current ? { ...current, name: event.target.value } : current))}
                />
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="project-description">Project Description</Label>
              <textarea
                id="project-description"
                rows={4}
                value={formState.description}
                onChange={(event) =>
                  setFormState((current) => (current ? { ...current, description: event.target.value } : current))
                }
                className="flex w-full resize-none rounded-md border border-black/20 bg-white px-3 py-2 text-sm text-black placeholder:text-black/45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2a63f5]"
                placeholder="Describe project scope"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="project-testers">Testing Team Members (semicolon separated)</Label>
              <Input
                id="project-testers"
                value={formState.teamMembers}
                onChange={(event) =>
                  setFormState((current) => (current ? { ...current, teamMembers: event.target.value } : current))
                }
                placeholder="qa1@company.com;qa2@company.com"
              />
            </div>

            <div className="space-y-3 rounded-lg border border-black/10 p-4">
              <div className="space-y-2">
                <Label htmlFor="project-url">Project URL</Label>
                <div className="flex flex-col gap-2 sm:flex-row">
                  <Input
                    id="project-url"
                    type="url"
                    value={formState.url}
                    onChange={(event) => {
                      const nextUrl = event.target.value;
                      setFormState((current) => (current ? { ...current, url: nextUrl } : current));
                      setIsLaunched(false);
                      setIsProceedConfirmed(false);
                    }}
                    placeholder="https://your-app.com"
                    className="flex-1"
                  />
                  <Button type="button" onClick={handleLaunch} className="sm:min-w-28">
                    <Play className="h-4 w-4" />
                    Launch
                  </Button>
                </div>
              </div>

              {isLaunched ? (
                <div className="flex flex-wrap items-center gap-2 rounded-md border border-[#2a63f5]/25 bg-[#2a63f5]/10 p-3">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => toast.success("Ticket raised for this project.")}
                  >
                    <Ticket className="h-4 w-4" />
                    Raise Ticket
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    onClick={() => {
                      setIsProceedConfirmed(true);
                      toast.success("Proceed confirmed. Upload and ingest are now enabled.");
                    }}
                  >
                    <ArrowRight className="h-4 w-4" />
                    Proceed
                  </Button>
                </div>
              ) : null}
            </div>

            <div className="space-y-4 rounded-lg border border-black/10 p-4">
              <div>
                <p className="text-sm font-semibold text-black">Uploaded Documents</p>
                <p className="text-xs text-black/60">
                  Uploads are category-wise. BRD/FSD/WBS/Credentials/Assumptions accept PDF. Swagger Docs accept YAML or JSON. Max {MAX_DOCUMENT_SIZE_MB}MB per file.
                </p>
                {!canIngestAndAddDocuments ? (
                  <p className="mt-1 text-xs font-medium text-[#2a63f5]">
                    Launch URL and click Proceed to enable document upload and ingestion.
                  </p>
                ) : null}
              </div>

              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                {DOCUMENT_CATEGORIES.map((category) => {
                  const files = formState.documents[category];

                  return (
                    <div key={category} className="rounded-lg border border-black/10 bg-white p-3">
                      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                        <div>
                          <p className="text-sm font-semibold text-black">{formatCategoryLabel(category)}</p>
                          <p className="text-xs text-black/55">
                            {getAcceptedFormatLabel(category)} only | Multiple files | Max {MAX_DOCUMENT_SIZE_MB}MB each
                          </p>
                        </div>

                        <label
                          className={cn(
                            "inline-flex items-center gap-2 rounded-md border border-black/20 bg-white px-2.5 py-1.5 text-xs",
                            canIngestAndAddDocuments
                              ? "cursor-pointer text-black hover:bg-[#2a63f5]/10"
                              : "cursor-not-allowed text-black/45 opacity-60"
                          )}
                        >
                          <Upload className="h-3.5 w-3.5" />
                          Upload
                          <input
                            type="file"
                            accept={category === "SwaggerDocs" ? ".yaml,.yml,.json,application/yaml,text/yaml,application/json" : "application/pdf,.pdf"}
                            multiple
                            disabled={!canIngestAndAddDocuments}
                            className="hidden"
                            onChange={(event) => handleDocumentUpload(category, event)}
                          />
                        </label>
                      </div>

                      {files.length === 0 ? (
                        <p className="rounded-md border border-dashed border-black/15 bg-[#2a63f5]/5 px-3 py-2 text-xs text-black/55">
                          No files uploaded.
                        </p>
                      ) : (
                        <ul className="space-y-2">
                          {files.map((documentName, index) => (
                            <li
                              key={`${category}-${documentName}-${index}`}
                              className="flex items-center justify-between rounded-md border border-black/10 bg-[#2a63f5]/5 px-3 py-2 text-sm"
                            >
                              <span className="inline-flex min-w-0 items-center gap-2">
                                <FileText className="h-4 w-4 shrink-0 text-[#2a63f5]" />
                                <span className="truncate text-black">{documentName}</span>
                              </span>

                              <button
                                type="button"
                                className="inline-flex items-center rounded-md border border-red-200 bg-white p-1.5 text-red-600 hover:bg-red-50"
                                onClick={() => removeDocument(category, documentName, index)}
                                aria-label={`Delete ${documentName}`}
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="flex justify-end gap-2">
              <Button type="button" variant="secondary" onClick={handleIngest} disabled={!canIngestAndAddDocuments}>
                Ingest
              </Button>
              <Button onClick={handleSave}>
                <Save className="h-4 w-4" />
                Save Changes
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
