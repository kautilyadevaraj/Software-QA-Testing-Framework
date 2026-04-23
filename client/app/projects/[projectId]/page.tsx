"use client";

import Link from "next/link";
import { ChangeEvent, useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft,
  ArrowRight,
  DatabaseZap,
  FileText,
  Link2,
  Play,
  Save,
  TestTubeDiagonal,
  Ticket,
  Trash2,
  Upload,
  CheckCircle2,
  Loader2,
  Terminal,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { markProjectVerified } from "@/lib/api";
import {
  ApiError,
  addProjectMember,
  connectProjectToJira,
  createTicket,
  deleteProjectDocument,
  getCurrentUser,
  getProject,
  getProjectJiraConfig,
  ingestProject,
  launchProject,
  listProjectDocuments,
  listProjectMembers,
  removeProjectMember,
  searchUsers,
  transferProjectOwnership,
  updateProject,
  uploadProjectDocuments,
  verifyProject,
  getProjectCredentials,
  runProjectPlaywright,
  type JiraConfig,
} from "@/lib/api";
import type { MemberResponse, UserSearchResponse } from "@/lib/api";
import {
  DOCUMENT_CATEGORIES,
  DocumentCategory,
  ProjectDocuments,
  ProjectRecord,
  REQUIRED_DOCUMENT_CATEGORIES,
  SINGLE_UPLOAD_CATEGORIES,
  createEmptyDocuments,
  mapDocumentsFromApi,
  mapProjectFromApi,
} from "@/lib/projects";
import { cn } from "@/lib/utils";
import { RaiseTicketModal } from "@/components/raise-ticket-modal";

type ActiveTab = "qa" | "configuration";

type ProjectFormState = {
  name: string;
  description: string;
  url: string;
};

const MAX_DOCUMENT_SIZE_MB = 20;
const MAX_DOCUMENT_SIZE_BYTES = MAX_DOCUMENT_SIZE_MB * 1024 * 1024;

function toFormState(project: ProjectRecord): ProjectFormState {
  return {
    name: project.name,
    description: project.description,
    url: project.url ?? "",
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

function formatCategoryLabel(category: DocumentCategory) {
  const baseLabel = category === "SwaggerDocs" ? "Swagger Docs" : category;
  const isRequired = REQUIRED_DOCUMENT_CATEGORIES.includes(category);
  return `${baseLabel}${isRequired ? "*" : ""}`;
}

function isCsvFile(file: File) {
  const isCsvByType =
    file.type === "text/csv" || file.type === "application/vnd.ms-excel";
  const isCsvByName = file.name.toLowerCase().endsWith(".csv");
  return isCsvByType || isCsvByName;
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
  const isYamlByType =
    file.type === "application/yaml" || file.type === "text/yaml";
  const isJsonByType = file.type === "application/json";
  return isYamlByName || isJsonByName || isYamlByType || isJsonByType;
}

function getAcceptedFormatLabel(category: DocumentCategory) {
  if (category === "SwaggerDocs") return "YAML or JSON";
  if (category === "Credentials") return "CSV";
  return "PDF";
}

export default function ProjectDetailsPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = params.projectId;
  const router = useRouter();

  const [project, setProject] = useState<ProjectRecord | null>(null);
  const [formState, setFormState] = useState<ProjectFormState | null>(null);
  const [documents, setDocuments] = useState<ProjectDocuments>(
    createEmptyDocuments(),
  );
  const [members, setMembers] = useState<MemberResponse[]>([]);
  const [currentUserEmail, setCurrentUserEmail] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<UserSearchResponse[]>([]);
  const [isSearching, setIsSearching] = useState(false);

  const [activeTab, setActiveTab] = useState<ActiveTab>("configuration");
  const [isLoading, setIsLoading] = useState(true);
  const [isLaunched, setIsLaunched] = useState(false);
  const [isProceedConfirmed, setIsProceedConfirmed] = useState(false);
  const [isIngestionStarted, setIsIngestionStarted] = useState(false);
  const [isBackendSynced, setIsBackendSynced] = useState(false);
  const [credentials, setCredentials] = useState<any[]>([]);
  const [progress, setProgress] = useState(0);
  const [logs, setLogs] = useState<string[]>([]);
  const [status, setStatus] = useState("idle");
  const allVerified =
    isBackendSynced &&
    credentials.length > 0 &&
    credentials.every((c) => c.verified === true);

  // Jira integration state
  const [jiraConfig, setJiraConfig] = useState<JiraConfig | null>(null);
  const [isConnectingJira, setIsConnectingJira] = useState(false);
  const [raiseTicketModal, setRaiseTicketModal] = useState<{
    open: boolean;
    defaultTitle: string;
    defaultDescription: string;
    raisedFrom: "url_section" | "credentials_section";
  }>({
    open: false,
    defaultTitle: "",
    defaultDescription: "",
    raisedFrom: "url_section",
  });

  const loadProjectState = useCallback(async () => {
    setIsLoading(true);

    try {
      const [projectResponse, documentsResponse, membersResponse, currentUser, jiraConfigResponse] =
        await Promise.all([
          getProject(projectId),
          listProjectDocuments(projectId),
          listProjectMembers(projectId),
          getCurrentUser(),
          getProjectJiraConfig(projectId),
        ]);

      setCurrentUserEmail(currentUser.email);
      setJiraConfig(jiraConfigResponse);

      const mappedProject = mapProjectFromApi(projectResponse);
      setProject(mappedProject);
      setFormState(toFormState(mappedProject));
      setDocuments(mapDocumentsFromApi(documentsResponse.items));
      setMembers(membersResponse);
      setIsLaunched(Boolean(mappedProject.url));
      setIsProceedConfirmed(mappedProject.is_verified);
    } catch (error) {
      const message =
        error instanceof ApiError ? error.message : "Unable to load project.";
      toast.error(message);
      setProject(null);
      setFormState(null);
      setDocuments(createEmptyDocuments());
      setIsLaunched(false);
      setIsProceedConfirmed(false);
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void loadProjectState();
  }, [loadProjectState]);

  // ── Jira handlers ──────────────────────────────────────────────────────────

  const handleConnectJira = async () => {
    if (!project) return;
    setIsConnectingJira(true);
    try {
      const result = await connectProjectToJira(project.id);
      setJiraConfig(result);
      if (result.already_existed) {
        toast.info(`Already connected — Jira project key: ${result.jira_project_key}`);
      } else {
        toast.success(`Connected! Jira project key: ${result.jira_project_key}`);
      }
    } catch (error) {
      const message =
        error instanceof ApiError ? error.message : "Failed to connect to Jira.";
      toast.error(message);
    } finally {
      setIsConnectingJira(false);
    }
  };

  const openRaiseTicket = (
    raisedFrom: "url_section" | "credentials_section",
    defaultTitle: string,
    defaultDescription: string,
  ) => {
    setRaiseTicketModal({ open: true, defaultTitle, defaultDescription, raisedFrom });
  };

  useEffect(() => {
    if (activeTab === "qa" || isIngestionStarted) {
      fetchCredentials();

      const interval = setInterval(fetchCredentials, 3000);
      return () => clearInterval(interval);
    }
  }, [activeTab, isIngestionStarted]);

  useEffect(() => {
    if (!allVerified || !isIngestionStarted) return;

    const interval = setInterval(async () => {
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/v1/projects/${projectId}/extract-status`,
        {
          credentials: "include",
        },
      );
      const data = await res.json();

      setProgress(data.progress || 0);
      setLogs(data.logs || []);
      setStatus(data.status || "idle");

      if (data.status === "completed") {
        clearInterval(interval);
        setTimeout(() => {
          setActiveTab("qa");
        }, 3000);
      }
    }, 2000);

    return () => clearInterval(interval);
  }, [allVerified, isIngestionStarted, projectId]);

  const canIngestAndAddDocuments = isLaunched && isProceedConfirmed;

  const fetchCredentials = async () => {
    if (!projectId) return;

    try {
      const data = await getProjectCredentials(projectId);

      if (Array.isArray(data)) {
        console.log("DEBUG credentials:", data);
        setCredentials(data);
        setIsBackendSynced(true);
      } else {
        setCredentials([]);
      }
    } catch (err) {
      console.error("Failed to fetch credentials:", err);
      setCredentials([]);
    }
  };

  const handleSearchUsers = async () => {
    if (!searchQuery.trim() || searchQuery.length < 3) return;
    setIsSearching(true);
    try {
      const results = await searchUsers(searchQuery);
      setSearchResults(results);
    } catch (error) {
      toast.error("Failed to search users");
    } finally {
      setIsSearching(false);
    }
  };

  const handleAddMember = async (email: string) => {
    try {
      const newMember = await addProjectMember(projectId, email);
      setMembers((prev) => [...prev, newMember]);
      setSearchResults([]);
      setSearchQuery("");
      toast.success("Member added.");
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "Failed to add member",
      );
    }
  };

  const handleRemoveMember = async (memberId: string) => {
    try {
      await removeProjectMember(projectId, memberId);
      setMembers((prev) => prev.filter((m) => m.id !== memberId));
      toast.success("Member removed.");
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : "Failed to remove member",
      );
    }
  };

  const handleTransferOwnership = async (memberId: string) => {
    try {
      await transferProjectOwnership(projectId, memberId);
      const membersResponse = await listProjectMembers(projectId);
      setMembers(membersResponse);
      toast.success("Ownership transferred.");
    } catch (error) {
      toast.error(
        error instanceof ApiError
          ? error.message
          : "Failed to transfer ownership",
      );
    }
  };

  const handleSave = async () => {
    if (!project || !formState) {
      return;
    }

    if (!formState.name.trim()) {
      toast.error("Project name is required.");
      return;
    }

    for (const requiredCategory of REQUIRED_DOCUMENT_CATEGORIES) {
      if (documents[requiredCategory].length === 0) {
        toast.error(
          `${formatCategoryLabel(requiredCategory)} requires at least one ${getAcceptedFormatLabel(requiredCategory)} file.`,
        );
        return;
      }
    }

    try {
      const updated = await updateProject(project.id, {
        name: formState.name.trim(),
        description: formState.description.trim(),
        status: project.status,
        url: formState.url.trim() ? formState.url.trim() : null,
      });

      const mapped = mapProjectFromApi(updated);
      setProject(mapped);
      setFormState(toFormState(mapped));
      toast.success("Project configuration updated.");
    } catch (error) {
      const message =
        error instanceof ApiError ? error.message : "Unable to update project.";
      toast.error(message);
    }
  };

  const handleVerify = async (cred: any) => {
    try {
      await runProjectPlaywright(projectId, cred);
      toast.success("Playwright launched");
    } catch (err) {
      toast.error("Failed to launch Playwright");
    }
  };

  const startExtraction = async () => {
    await fetch(
      `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/v1/projects/${projectId}/extract-pdfs`,
      {
        method: "POST",
        credentials: "include",
      },
    );
  };

  const handleMarkVerified = async (cred: any) => {
    try {
      await markProjectVerified(projectId, cred.username);

      toast.success("Updated verification status");

      // ✅ ONLY backend source of truth
      await fetchCredentials();
    } catch (err) {
      toast.error("Failed to mark verified");
    }
  };

  const handleReverify = async (cred: any) => {
    try {
      await handleMarkVerified(cred); // toggle from backend
      toast.success("Reverify started");
    } catch (err) {
      toast.error("Failed to reverify");
    }
  };

  const handleDocumentUpload = async (
    category: DocumentCategory,
    event: ChangeEvent<HTMLInputElement>,
  ) => {
    const files = Array.from(event.target.files ?? []);
    if (!files.length || !project) {
      return;
    }

    const accepted: File[] = [];

    for (const file of files) {
      const isAllowed =
        category === "SwaggerDocs"
          ? isSwaggerFile(file)
          : category === "Credentials"
            ? isCsvFile(file)
            : isPdfFile(file);
      if (!isAllowed) {
        toast.error(
          `${file.name} is not a valid ${getAcceptedFormatLabel(category)} file.`,
        );
        continue;
      }

      if (file.size > MAX_DOCUMENT_SIZE_BYTES) {
        toast.error(`${file.name} exceeds ${MAX_DOCUMENT_SIZE_MB}MB.`);
        continue;
      }

      accepted.push(file);
    }

    if (accepted.length === 0) {
      event.target.value = "";
      return;
    }

    try {
      const response = await uploadProjectDocuments(
        project.id,
        category,
        accepted,
      );
      const uploadedById = new Map(
        response.items.map((item) => [item.id, item]),
      );

      setDocuments((current) => {
        const existing = current[category];
        const nextForCategory = [...existing];

        for (const file of accepted) {
          const match = response.items.find(
            (item) =>
              item.original_filename === file.name &&
              !existing.some((doc) => doc.id === item.id),
          );
          if (!match) {
            continue;
          }

          if (uploadedById.has(match.id)) {
            nextForCategory.push({
              id: match.id,
              name: match.original_filename,
              contentType: match.content_type,
              sizeBytes: match.size_bytes,
              createdAt: match.created_at,
            });
          }
        }

        return {
          ...current,
          [category]: nextForCategory,
        };
      });

      setIsIngestionStarted(false);
      toast.success("Document upload completed.");
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.message
          : "Unable to upload documents.";
      toast.error(message);
    } finally {
      event.target.value = "";
    }
  };

  const handleLaunch = async () => {
    if (!project || !formState?.url.trim()) {
      toast.error("Project URL is required to launch.");
      return;
    }

    try {
      const launch = await launchProject(project.id, formState.url.trim());
      setIsLaunched(true);
      setIsProceedConfirmed(launch.is_verified);
      window.open(formState.url.trim(), "_blank");
      toast.success("Project URL launched.");
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.message
          : "Unable to launch project URL.";
      toast.error(message);
    }
  };

  const handleSaveAndIngest = async () => {
    if (!project || !formState || !canIngestAndAddDocuments) {
      return;
    }

    if (!formState.name.trim()) {
      toast.error("Project name is required.");
      return;
    }

    for (const requiredCategory of REQUIRED_DOCUMENT_CATEGORIES) {
      if (documents[requiredCategory].length === 0) {
        toast.error(
          `${formatCategoryLabel(requiredCategory)} requires at least one ${getAcceptedFormatLabel(requiredCategory)} file.`,
        );
        return;
      }
    }

    try {
      // 1. Save project metadata
      const updated = await updateProject(project.id, {
        name: formState.name.trim(),
        description: formState.description.trim(),
        status: project.status,
        url: formState.url.trim() ? formState.url.trim() : null,
      });
      const mapped = mapProjectFromApi(updated);
      setProject(mapped);
      setFormState(toFormState(mapped));

      // 2. Trigger ingestion
      await ingestProject(project.id);
      toast.success("Saved and ingestion started.");

      // ✅ RESET UI STATE (IMPORTANT)
      setCredentials([]);
      setIsBackendSynced(false);
      setIsIngestionStarted(true);

      // OPTIONAL: avoid flicker of "All verified"
      setTimeout(async () => {
        await fetchCredentials();
      }, 500);
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.message
          : "Unable to save and ingest.";
      toast.error(message);
    }
  };

  const removeDocument = async (
    category: DocumentCategory,
    documentId: string,
    documentName: string,
  ) => {
    if (!project) {
      return;
    }

    try {
      await deleteProjectDocument(project.id, documentId);
      setDocuments((current) => ({
        ...current,
        [category]: current[category].filter(
          (document) => document.id !== documentId,
        ),
      }));
      setIsIngestionStarted(false);
      toast.success(`${documentName} deleted.`);
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.message
          : "Unable to delete document.";
      toast.error(message);
    }
  };

  const renderCredentialsUI = () => (
    <div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {credentials.length === 0 ? (
          <p className="text-sm text-black/60 text-center">
            No credentials found. Upload CSV and click Save & Ingest.
          </p>
        ) : allVerified ? (
          <div className="md:col-span-3 border p-6 rounded-lg bg-white shadow-sm flex flex-col items-center">
            <div className="flex items-center gap-2 mb-2">
              <CheckCircle2 className="h-5 w-5 text-[#2a63f5]" />
              <p className="text-base font-semibold text-black">
                All credentials verified
              </p>
            </div>
            
            <p className="text-sm text-black/60 mb-6 text-center max-w-sm">
              Your test environment is secured and ready. You can now start the extraction process.
            </p>

            <div className="w-full max-w-lg">
              {status === "idle" && (
                <Button onClick={startExtraction} className="w-full bg-[#2a63f5] hover:bg-[#2a63f5]/90">
                  <Play className="mr-2 h-4 w-4" /> Start Processing
                </Button>
              )}

              {status !== "idle" && (() => {
                  const expectedDocs = [
                    ...[...(documents.BRD || []), ...(documents.FSD || []), ...(documents.WBS || []), ...(documents.Assumptions || [])].map(d => d.name),
                    ...(documents.SwaggerDocs || []).map(d => `Swagger ${d.name}`)
                  ];

                  const getDocStatus = (docName: string, logsList: string[]) => {
                    const isSwagger = docName.startsWith("Swagger ");
                    const baseName = docName.replace("Swagger ", "");
                    
                    const parseStr = isSwagger ? `Parsing Swagger ${baseName}` : `Parsing ${baseName}`;
                    const successStr = isSwagger ? `Successfully parsed Swagger ${baseName}` : `Successfully parsed ${baseName}`;
                    const failStr = isSwagger ? `Failed parsing Swagger ${baseName}` : `Failed ${baseName}`;
                    const chunkingStr = `Generating chunks and embeddings for ${baseName}`;

                    const successLog = logsList.find(l => l.startsWith(successStr));
                    if (successLog) return 'completed';

                    const failLog = logsList.find(l => l.startsWith(failStr));
                    if (failLog) return 'failed';

                    if (!isSwagger) {
                      const chunkingLog = logsList.find(l => l.startsWith(chunkingStr));
                      if (chunkingLog) return 'chunking';
                    }

                    const parseLog = logsList.find(l => l.startsWith(parseStr));
                    if (parseLog) return 'processing';

                    return 'pending';
                  };

                  const tasks = expectedDocs.map((docName, index) => {
                    return {
                      id: index + 1,
                      label: docName,
                      status: getDocStatus(docName, logs || [])
                    };
                  });

                  return (
                    <div className="border rounded-xl p-8 bg-white shadow-sm flex flex-col items-start justify-center transition-all duration-500 w-full mt-4">
                      
                      {/* Overall Progress Tracker */}
                      <div className="w-full mb-8">
                         <div className="flex items-center justify-between mb-2">
                           <h3 className="text-lg font-bold text-gray-900 flex items-center gap-2">
                             {status === "completed" ? (
                               <><CheckCircle2 className="text-emerald-500 h-6 w-6" /> Extraction Complete!</>
                             ) : (
                               <><Loader2 className="h-5 w-5 text-[#2a63f5] animate-spin" style={{ animationDuration: '1s' }} /> Processing Documents</>
                             )}
                           </h3>
                           <span className="text-sm font-semibold text-gray-500">{progress}% Completed</span>
                         </div>
                         <div className="h-2 w-full overflow-hidden rounded-full bg-gray-100">
                           <div
                             className="h-full bg-[#2a63f5] transition-all duration-500 ease-out"
                             style={{ width: `${progress}%` }}
                           />
                         </div>
                      </div>

                      {/* Step-by-step Tasks Tracker */}
                      <div className="flex flex-col gap-4 w-full text-left ml-2">
                        {tasks.map((task) => (
                           <div key={task.id} className="flex items-center gap-4">
                              {task.status === 'completed' ? (
                                 <div className="w-7 h-7 rounded-full bg-[#2a63f5] text-white flex items-center justify-center shrink-0 shadow-sm border border-transparent transition-all">
                                   <svg className="w-4 h-4 animate-in zoom-in duration-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}><path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" /></svg>
                                 </div>
                              ) : task.status === 'processing' ? (
                                 <div className="relative w-7 h-7 flex items-center justify-center shrink-0">
                                    <div className="absolute inset-0 rounded-full border-[2.5px] border-[#2a63f5]/20 border-t-[#2a63f5] animate-spin" style={{ animationDuration: '1s' }}></div>
                                    <span className="font-semibold text-xs text-[#2a63f5] z-10">{task.id}</span>
                                 </div>
                              ) : task.status === 'chunking' ? (
                                 <div className="relative w-7 h-7 flex items-center justify-center shrink-0">
                                    <div className="absolute inset-0 rounded-full border-[2.5px] border-[#8b5cf6]/20 border-t-[#8b5cf6] animate-spin" style={{ animationDuration: '1s' }}></div>
                                    <span className="font-semibold text-xs text-[#8b5cf6] z-10">{task.id}</span>
                                 </div>
                              ) : task.status === 'failed' ? (
                                 <div className="w-7 h-7 rounded-full bg-red-100 text-red-600 flex items-center justify-center shrink-0 shadow-sm border border-transparent transition-all">
                                   <span className="font-bold text-[10px]">X</span>
                                 </div>
                              ) : (
                                 <div className="w-7 h-7 rounded-full border-[2px] border-gray-200 text-gray-400 flex items-center justify-center shrink-0 transition-all font-semibold text-xs bg-gray-50">
                                   {task.id}
                                 </div>
                              )}
                              <span className={`text-[15px] font-medium transition-all duration-300 ${task.status === 'completed' ? 'text-gray-500' : task.status === 'failed' ? 'text-red-500' : (task.status === 'processing' || task.status === 'chunking') ? 'text-gray-900' : 'text-gray-400'}`}>
                                 {task.status === 'completed' ? `Successfully processed ${task.label}` :
                                  task.status === 'failed' ? `Failed to process ${task.label}` :
                                  task.status === 'chunking' ? `Chunking & generating embeddings for ${task.label}...` :
                                  task.status === 'processing' ? `Parsing ${task.label}...` :
                                  `Queued for parsing ${task.label}`}
                              </span>
                           </div>
                        ))}
                      </div>

                      {/* Final Redirection Indicator */}
                      {status === "completed" && (
                        <div className="mt-8 flex items-center justify-center gap-2 text-sm text-gray-500 animate-pulse bg-gray-50 rounded-lg py-3 w-full border border-gray-100 transition-all fade-in zoom-in">
                          <Loader2 className="h-4 w-4 animate-spin" />
                          Redirecting to QA page in 3 seconds...
                        </div>
                      )}
                    </div>
                  );
              })()}
            </div>
          </div>
        ) : (
          credentials.map((cred, index) => (
            <div
              key={index}
              className="border p-4 rounded-lg bg-white shadow"
            >
              <p className="text-sm font-semibold">{cred.username}</p>
              <p className="text-xs text-gray-500">{cred.role}</p>

              {/* Row 1: verification actions */}
              <div className="mt-3 flex flex-wrap gap-2">
                {!cred.verified ? (
                  <>
                    <Button size="sm" onClick={() => handleVerify(cred)}>
                      Launch
                    </Button>

                    <Button size="sm" onClick={() => handleMarkVerified(cred)}>
                      Mark Verified
                    </Button>
                  </>
                ) : (
                  <Button size="sm" onClick={() => handleReverify(cred)}>
                    Reverify
                  </Button>
                )}
              </div>

              {/* Row 2: Raise Ticket — full width so it never overflows */}
              <div className="mt-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!jiraConfig?.connected}
                  title={
                    !jiraConfig?.connected
                      ? "Connect to Jira first to raise tickets"
                      : `Raise a Jira ticket for ${cred.username}`
                  }
                  onClick={() =>
                    openRaiseTicket(
                      "credentials_section",
                      `Login Failure — ${cred.username} (${cred.role})`,
                      `Credential verification issue for user ${cred.username} (role: ${cred.role}, auth: ${cred.auth_type ?? "N/A"}). Endpoint: ${cred.endpoint ?? "N/A"}. Raised from credentials verification step.`,
                    )
                  }
                  className="w-full justify-center gap-1.5 border-[#2a63f5]/30 text-[#2a63f5] hover:bg-[#2a63f5]/5 disabled:opacity-40"
                >
                  <Ticket className="h-3.5 w-3.5" />
                  Raise Ticket
                </Button>
              </div>


              {cred.verified && (
                <p className="text-green-600 text-xs mt-2">Verified</p>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );

  if (isLoading) {
    return (
      <Card className="border-black/10 bg-white">
        <CardHeader>
          <CardTitle className="text-black">Loading project...</CardTitle>
        </CardHeader>
      </Card>
    );
  }

  if (!project || !formState) {
    return (
      <Card className="border-black/10 bg-white">
        <CardHeader>
          <CardTitle className="text-black">Project not found</CardTitle>
          <CardDescription>
            The requested project does not exist or was removed.
          </CardDescription>
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
    <>
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
            {project.id} | Created {formatDate(project.createdAt)}
          </p>
        </div>

        <div className="flex flex-col items-start gap-2 sm:items-end">
          <div className="rounded-lg border border-black/10 bg-white px-3 py-2 text-sm text-black/70">
            Status:{" "}
            <span
              className={cn(
                "font-semibold",
                project.status === "Active" ? "text-[#2a63f5]" : "text-black",
              )}
            >
              {project.status}
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant={activeTab === "configuration" ? "default" : "outline"}
              onClick={() => setActiveTab("configuration")}
            >
              Project Configuration
            </Button>
            <Button
              variant={activeTab === "qa" ? "default" : "outline"}
              onClick={() => setActiveTab("qa")}
            >
              QA Testing
            </Button>
          </div>
        </div>
      </div>

      {activeTab === "qa" ? (
        <Card className="border-black/10 bg-white">
          <CardHeader>
            <CardTitle className="text-black">QA Testing</CardTitle>
            <CardDescription>
              Execution console will be available here.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <div className="rounded-lg border border-black/10 bg-[#2a63f5]/5 p-4">
                <p className="text-xs uppercase tracking-wide text-black/60">
                  Suite Status
                </p>
                <p className="mt-2 text-lg font-semibold text-black">Pending</p>
              </div>
              <div className="rounded-lg border border-black/10 bg-[#2a63f5]/5 p-4">
                <p className="text-xs uppercase tracking-wide text-black/60">
                  Total Cases
                </p>
                <p className="mt-2 text-lg font-semibold text-black">0</p>
              </div>
              <div className="rounded-lg border border-black/10 bg-[#2a63f5]/5 p-4">
                <p className="text-xs uppercase tracking-wide text-black/60">
                  Last Run
                </p>
                <p className="mt-2 text-lg font-semibold text-black">
                  Not Started
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      ) : (
        <Card className="border-black/10 bg-white">
          <CardHeader>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <CardTitle className="text-black">Project Configuration</CardTitle>
                <CardDescription>
                  Edit name, description, testing team, and documents.
                </CardDescription>
              </div>

              {/* Connect to Jira — 3-state button */}
              {jiraConfig?.connected ? (
                <div className="inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700">
                  <span className="h-2 w-2 rounded-full bg-emerald-500" />
                  Connected · Key:{" "}
                  <span className="font-bold">{jiraConfig.jira_project_key}</span>
                </div>
              ) : (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => void handleConnectJira()}
                  disabled={isConnectingJira}
                  className="gap-2 border-[#2a63f5]/30 text-[#2a63f5] hover:bg-[#2a63f5]/5"
                >
                  {isConnectingJira ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Link2 className="h-3.5 w-3.5" />
                  )}
                  {isConnectingJira ? "Connecting…" : "Connect to Jira"}
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="grid grid-cols-1 gap-4">
              <div className="space-y-2">
                <Label htmlFor="project-name">Project Name</Label>
                <Input
                  id="project-name"
                  value={formState.name}
                  onChange={(event) =>
                    setFormState((current) =>
                      current
                        ? { ...current, name: event.target.value }
                        : current,
                    )
                  }
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
                  setFormState((current) =>
                    current
                      ? { ...current, description: event.target.value }
                      : current,
                  )
                }
                className="flex w-full resize-none rounded-md border border-black/20 bg-white px-3 py-2 text-sm text-black placeholder:text-black/45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2a63f5]"
                placeholder="Describe project scope"
              />
            </div>

            <div className="space-y-3 rounded-lg border border-black/10 p-4">
              <div className="space-y-2">
                <Label>Testing Team Members</Label>
                <div className="flex flex-col gap-2 sm:flex-row">
                  <Input
                    placeholder="Search by email..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        void handleSearchUsers();
                      }
                    }}
                  />
                  <Button
                    type="button"
                    onClick={() => void handleSearchUsers()}
                    disabled={isSearching}
                  >
                    {isSearching ? "Searching..." : "Search"}
                  </Button>
                </div>
                {searchResults.length > 0 && (
                  <div className="rounded-md border border-black/10 bg-white p-2">
                    <p className="mb-2 text-xs font-semibold text-black/60">
                      Search Results
                    </p>
                    <ul className="space-y-1">
                      {searchResults.map((u) => (
                        <li
                          key={u.id}
                          className="flex items-center justify-between rounded bg-[#2a63f5]/5 px-2 py-1 text-sm"
                        >
                          <span>{u.email}</span>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => void handleAddMember(u.email)}
                            className="h-7 text-[#2a63f5]"
                          >
                            Add
                          </Button>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Derive whether the current logged-in user is the OWNER of this project */}
                {(() => {
                  const isCurrentUserOwner = members.some(
                    (m) => m.email === currentUserEmail && m.role === "OWNER",
                  );
                  return (
                    <div className="mt-4 space-y-2">
                      <p className="text-xs font-semibold uppercase tracking-wide text-black/60">
                        Current Members
                      </p>
                      {members.map((member) => (
                        <div
                          key={member.id}
                          className="flex items-center justify-between rounded-md border border-black/10 px-3 py-2 text-sm bg-white"
                        >
                          <div>
                            <span className="font-medium text-black">
                              {member.email}
                            </span>
                            <span
                              className={cn(
                                "ml-2 inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold",
                                member.role === "OWNER"
                                  ? "bg-amber-100 text-amber-700"
                                  : "bg-blue-100 text-[#2a63f5]",
                              )}
                            >
                              {member.role}
                            </span>
                          </div>
                          {/* Only the OWNER can manage other members */}
                          {isCurrentUserOwner && member.role === "TESTER" && (
                            <div className="flex items-center gap-2">
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() =>
                                  void handleTransferOwnership(member.id)
                                }
                                className="h-7 text-xs"
                              >
                                Make Owner
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() =>
                                  void handleRemoveMember(member.id)
                                }
                                className="h-7 text-red-600 border-red-200 hover:bg-red-50"
                              >
                                Remove
                              </Button>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  );
                })()}
              </div>
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
                      setFormState((current) =>
                        current ? { ...current, url: nextUrl } : current,
                      );
                      setIsLaunched(false);
                      setIsProceedConfirmed(false);
                    }}
                    placeholder="https://your-app.com"
                    className="flex-1"
                  />
                  <Button
                    type="button"
                    onClick={() => void handleLaunch()}
                    className="sm:min-w-28"
                  >
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
                    disabled={!jiraConfig?.connected}
                    title={
                      !jiraConfig?.connected
                        ? "Connect to Jira first to raise tickets"
                        : "Raise a ticket for this URL verification issue"
                    }
                    onClick={() =>
                      openRaiseTicket(
                        "url_section",
                        `URL Verification Issue — ${project.name}`,
                        `The project URL ${project.url || formState.url} was launched and requires attention. Raised from URL verification step.`,
                      )
                    }
                    className="gap-2 disabled:opacity-40"
                  >
                    <Ticket className="h-4 w-4" />
                    Raise Ticket
                  </Button>
                  {!isProceedConfirmed ? (
                    <Button
                      type="button"
                      size="sm"
                      onClick={async () => {
                        try {
                          const res = await verifyProject(project.id, true);
                          setIsProceedConfirmed(res.is_verified);
                          toast.success("Verified");
                        } catch {
                          toast.error("Verification failed");
                        }
                      }}
                    >
                      <ArrowRight className="h-4 w-4" />
                      Proceed
                    </Button>
                  ) : (
                    <Button
                      type="button"
                      size="sm"
                      disabled
                      className="bg-green-600 text-white"
                    >
                      ✔ Verified
                    </Button>
                  )}
                </div>
              ) : null}
            </div>

            <div className="space-y-4 rounded-lg border border-black/10 p-4">
              <div>
                <p className="text-sm font-semibold text-black">
                  Uploaded Documents
                </p>
                <p className="text-xs text-black/60">
                  Uploads are category-wise. BRD/FSD/WBS/Credentials/Assumptions
                  accept PDF. Swagger Docs accept YAML or JSON. Max{" "}
                  {MAX_DOCUMENT_SIZE_MB}MB per file.
                </p>
                {!canIngestAndAddDocuments ? (
                  <p className="mt-1 text-xs font-medium text-[#2a63f5]">
                    Launch URL and click Proceed to enable document upload and
                    ingestion.
                  </p>
                ) : null}
              </div>

              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                {DOCUMENT_CATEGORIES.map((category) => {
                  const files = documents[category];

                  return (
                    <div
                      key={category}
                      className="rounded-lg border border-black/10 bg-white p-3"
                    >
                      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                        <div>
                          <p className="text-sm font-semibold text-black">
                            {formatCategoryLabel(category)}
                          </p>
                          <p className="text-xs text-black/55">
                            {getAcceptedFormatLabel(category)}
                            {SINGLE_UPLOAD_CATEGORIES.includes(category)
                              ? " | Single file"
                              : " | Multiple files"}{" "}
                            | Max {MAX_DOCUMENT_SIZE_MB}MB each
                          </p>
                        </div>

                        <label
                          className={cn(
                            "inline-flex items-center gap-2 rounded-md border border-black/20 bg-white px-2.5 py-1.5 text-xs",
                            canIngestAndAddDocuments
                              ? "cursor-pointer text-black hover:bg-[#2a63f5]/10"
                              : "cursor-not-allowed text-black/45 opacity-60",
                          )}
                        >
                          <Upload className="h-3.5 w-3.5" />
                          Upload
                          <input
                            type="file"
                            accept={
                              category === "SwaggerDocs"
                                ? ".yaml,.yml,.json,application/yaml,text/yaml,application/json"
                                : category === "Credentials"
                                  ? ".csv,text/csv"
                                  : "application/pdf,.pdf"
                            }
                            multiple={
                              !SINGLE_UPLOAD_CATEGORIES.includes(category)
                            }
                            disabled={!canIngestAndAddDocuments}
                            className="hidden"
                            onChange={(event) =>
                              void handleDocumentUpload(category, event)
                            }
                          />
                        </label>
                      </div>

                      {files.length === 0 ? (
                        <p className="rounded-md border border-dashed border-black/15 bg-[#2a63f5]/5 px-3 py-2 text-xs text-black/55">
                          No files uploaded.
                        </p>
                      ) : (
                        <ul className="space-y-2">
                          {files.map((document) => (
                            <li
                              key={document.id}
                              className="flex items-center justify-between rounded-md border border-black/10 bg-[#2a63f5]/5 px-3 py-2 text-sm"
                            >
                              <span className="inline-flex min-w-0 items-center gap-2">
                                <FileText className="h-4 w-4 shrink-0 text-[#2a63f5]" />
                                <span className="truncate text-black">
                                  {document.name}
                                </span>
                              </span>

                              <button
                                type="button"
                                className="inline-flex items-center rounded-md border border-red-200 bg-white p-1.5 text-red-600 hover:bg-red-50"
                                onClick={() =>
                                  void removeDocument(
                                    category,
                                    document.id,
                                    document.name,
                                  )
                                }
                                aria-label={`Delete ${document.name}`}
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

            {isIngestionStarted ? (
              <div className="space-y-4 rounded-lg border border-black/10 p-4 bg-[#2a63f5]/5">
                <h3 className="text-lg font-semibold text-black">Credentials Verification &amp; Ingestion</h3>
                {renderCredentialsUI()}
              </div>
            ) : (
              <div className="flex justify-end gap-2">
                <Button onClick={() => void handleSave()}>
                  <Save className="h-4 w-4" />
                  Save Changes
                </Button>
                <Button
                  type="button"
                  onClick={() => void handleSaveAndIngest()}
                  disabled={!canIngestAndAddDocuments}
                  className="bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50"
                >
                  <DatabaseZap className="h-4 w-4" />
                  Save &amp; Ingest
                </Button>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>

    {/* Raise Ticket Modal — shared across URL section and credentials section */}
    <RaiseTicketModal
      open={raiseTicketModal.open}
      onClose={() =>
        setRaiseTicketModal((prev) => ({ ...prev, open: false }))
      }
      projectId={projectId}
      defaultTitle={raiseTicketModal.defaultTitle}
      defaultDescription={raiseTicketModal.defaultDescription}
      raisedFrom={raiseTicketModal.raisedFrom}
    />
    </>
  );
}
