const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");
const API_ROOT = `${API_BASE_URL}/api/v1`;

type ApiRequestOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
};

type ApiErrorPayload = {
  detail?: string | { msg?: string }[];
  error?: string;
};

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function isFormData(value: unknown): value is FormData {
  return typeof FormData !== "undefined" && value instanceof FormData;
}

function getErrorMessage(payload: ApiErrorPayload | null, fallback: string) {
  if (!payload) {
    return fallback;
  }

  if (typeof payload.error === "string" && payload.error.trim()) {
    return payload.error;
  }

  if (typeof payload.detail === "string" && payload.detail.trim()) {
    return payload.detail;
  }

  if (Array.isArray(payload.detail) && payload.detail.length > 0) {
    const first = payload.detail[0] as { msg?: string; loc?: (string | number)[] };
    if (first) {
      const msg = typeof first.msg === "string" ? first.msg : "";
      const loc = Array.isArray(first.loc)
        ? first.loc.filter((part) => part !== "body").join(" → ")
        : "";
      const combined = loc ? `${loc}: ${msg}` : msg;
      if (combined.trim()) return combined;
    }
  }

  return fallback;
}

async function request<T>(path: string, options: ApiRequestOptions = {}, allowRefresh = true): Promise<T> {
  const { body, headers, ...rest } = options;

  const finalHeaders = new Headers(headers ?? undefined);
  const init: RequestInit = {
    ...rest,
    headers: finalHeaders,
    credentials: "include",
  };

  if (body !== undefined) {
    if (isFormData(body)) {
      init.body = body;
    } else {
      if (!finalHeaders.has("Content-Type")) {
        finalHeaders.set("Content-Type", "application/json");
      }
      init.body = JSON.stringify(body);
    }
  }

  const response = await fetch(`${API_ROOT}${path}`, init);

  const isJson = response.headers.get("content-type")?.includes("application/json");
  const payload = isJson ? ((await response.json()) as unknown) : null;

  if (!response.ok) {
    if (response.status === 401 && allowRefresh && !path.startsWith("/auth/")) {
      const refreshResponse = await fetch(`${API_ROOT}/auth/refresh`, {
        method: "POST",
        credentials: "include",
      });
      if (refreshResponse.ok) {
        return request<T>(path, options, false);
      }
    }

    const message = getErrorMessage(payload as ApiErrorPayload | null, `Request failed with ${response.status}`);
    throw new ApiError(message, response.status);
  }

  return payload as T;
}

// ─── Auth types ────────────────────────────────────────────────────────────

export type AuthUser = {
  id: string;
  email: string;
  role: string;
  created_at: string;
};

export type AuthResponse = {
  user: AuthUser;
};

// ─── Project types ─────────────────────────────────────────────────────────

export type ProjectStatus = "Active" | "Draft" | "Blocked";

export type ProjectResponse = {
  id: string;
  owner_id: string;
  name: string;
  description: string;
  status: ProjectStatus;
  url: string | null;
  created_at: string;
  updated_at: string;
  is_verified: boolean;
  tester_ids: string[];
  tester_emails: string[];
};

export type ProjectListResponse = {
  items: ProjectResponse[];
  total: number;
  page: number;
  page_size: number;
};

export type DocumentResponse = {
  id: string;
  category: "BRD" | "FSD" | "WBS" | "SwaggerDocs" | "Credentials" | "Assumptions";
  original_filename: string;
  content_type: string;
  size_bytes: number;
  created_at: string;
};

export type DocumentListResponse = {
  items: DocumentResponse[];
};

export type LaunchResponse = {
  project_id: string;
  launched_url: string;
  is_verified: boolean;
  created_at: string;
  verified_at: string | null;
};

export type MemberResponse = {
  id: string;
  user_id: string;
  email: string;
  role: "OWNER" | "TESTER";
  joined_at: string;
};

export type UserSearchResponse = {
  id: string;
  email: string;
};

export type JiraConfig = {
  connected: boolean;
  jira_project_key: string | null;
  jira_project_id: string | null;
  already_existed?: boolean;
};

export type JiraTicketResponse = {
  id: string;
  project_id: string;
  jira_issue_key: string;
  jira_issue_id: string;
  title: string;
  description: string;
  issue_type: string;
  priority: string;
  status: string;
  raised_from: string;
  created_at: string;
};

export type RaiseTicketPayload = {
  title: string;
  description: string;
  issue_type: "Bug" | "Task" | "Story";
  priority: "High" | "Medium" | "Low";
  raised_from: "url_section" | "credentials_section";
};

export type ProjectCredential = {
  username: string;
  role: string | null;
  auth_type: string | null;
  endpoint: string | null;
  verified: boolean;
};

// ─── Scenario types ────────────────────────────────────────────────────────

export type ScenarioSource = "agent_1" | "agent_2" | "manual";
export type ScenarioStatus = "pending" | "completed";
export type RecordingStatus = "pending" | "in_progress" | "completed" | "failed";
export type ScenarioGenerationType =
  | "ALL"
  | "HLS"
  | "Functional"
  | "Technical"
  | "API"
  | "Security"
  | "Performance"
  | "Integration"
  | "Data"
  | "Compliance"
  | "Usability";
export type ScenarioAccessMode = "UI_ONLY_WEB" | "UI_AND_API" | "TECHNICAL_REVIEW";
export type ScenarioLevel = "HLS" | "DETAILED_HLS";

export type ScenarioGenerationSettings = {
  max_scenarios: number | null;
  scenario_types: ScenarioGenerationType[];
  access_mode: ScenarioAccessMode;
  scenario_level: ScenarioLevel;
  existing_scenarios?: PreviewScenario[];
};

export type PreviewScenario = {
  title: string;
  description: string;
  source: ScenarioSource;
};

export type HighLevelScenario = {
  id: string;
  project_id: string;
  title: string;
  description: string;
  source: ScenarioSource;
  status: ScenarioStatus;
  completed_by: string | null;
  completed_by_name: string | null;
  created_at: string;
  updated_at: string;
};

export type ScenarioListResponse = {
  scenarios: HighLevelScenario[];
};

// ui-discovery additional types

export type RecordingSessionResponse = {
  id: string;
  project_id: string;
  scenario_id: string;
  scenario_title: string;
  status: RecordingStatus;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  step_count: number;
};

export type RecordingSessionListResponse = {
  items: RecordingSessionResponse[];
};

export type LockScenariosResponse = {
  locked: boolean;
  sessions_created: number;
};

export type RecordingSetupResponse = {
  setup_command: string;
  recorder_token: string;
};

export type TriggerScenarioResponse = {
  triggered: boolean;
  scenario_id: string;
};

// ─── Auth API ──────────────────────────────────────────────────────────────

export async function signup(email: string, password: string) {
  return request<AuthResponse>("/auth/signup", {
    method: "POST",
    body: { email, password },
  });
}

export async function login(email: string, password: string) {
  return request<AuthResponse>("/auth/login", {
    method: "POST",
    body: { email, password },
  });
}

export async function logout() {
  return request<{ message: string }>("/auth/logout", {
    method: "POST",
  });
}

export async function getCurrentUser() {
  return request<AuthUser>("/auth/me", {
    method: "GET",
  });
}

// ─── Projects API ──────────────────────────────────────────────────────────

export async function listProjects(params: {
  sortBy: "id" | "name" | "created_at" | "status";
  sortDir: "asc" | "desc";
  page: number;
  pageSize: number;
}) {
  const query = new URLSearchParams({
    sort_by: params.sortBy,
    sort_dir: params.sortDir,
    page: String(params.page),
    page_size: String(params.pageSize),
  });

  return request<ProjectListResponse>(`/projects?${query.toString()}`, {
    method: "GET",
  });
}

export async function createProject(payload: {
  name: string;
  description: string;
  status: ProjectStatus;
  url: string | null;
}) {
  return request<ProjectResponse>("/projects", {
    method: "POST",
    body: payload,
  });
}

export async function getProject(projectId: string) {
  return request<ProjectResponse>(`/projects/${projectId}`, {
    method: "GET",
  });
}

export async function updateProject(
  projectId: string,
  payload: {
    name: string;
    description: string;
    status: ProjectStatus;
    url: string | null;
  }
) {
  return request<ProjectResponse>(`/projects/${projectId}`, {
    method: "PUT",
    body: payload,
  });
}

export async function deleteProject(projectId: string) {
  return request<{ message: string }>(`/projects/${projectId}`, {
    method: "DELETE",
  });
}

export async function listProjectDocuments(projectId: string) {
  return request<DocumentListResponse>(`/projects/${projectId}/documents`, {
    method: "GET",
  });
}

export async function uploadProjectDocuments(
  projectId: string,
  category: DocumentResponse["category"],
  files: File[]
) {
  const formData = new FormData();
  formData.set("category", category);
  for (const file of files) {
    formData.append("files", file);
  }

  return request<DocumentListResponse>(`/projects/${projectId}/documents`, {
    method: "POST",
    body: formData,
  });
}

export async function deleteProjectDocument(projectId: string, documentId: string) {
  return request<{ message: string }>(`/projects/${projectId}/documents/${documentId}`, {
    method: "DELETE",
  });
}

export async function launchProject(projectId: string, url: string) {
  return request<LaunchResponse>(`/projects/${projectId}/launch`, {
    method: "POST",
    body: { url },
  });
}

export async function verifyProject(projectId: string, verified: boolean) {
  return request<LaunchResponse>(`/projects/${projectId}/verify`, {
    method: "POST",
    body: { verified },
  });
}

export async function ingestProject(projectId: string) {
  return request<{ id: string; project_id: string; status: string; created_at: string }>(
    `/projects/${projectId}/ingest`,
    {
      method: "POST",
    }
  );
}

export async function createTicket(projectId: string, payload: RaiseTicketPayload) {
  return request<JiraTicketResponse>(`/projects/${projectId}/tickets`, {
    method: "POST",
    body: payload,
  });
}

export async function searchUsers(query: string) {
  return request<UserSearchResponse[]>(`/projects/users/search?query=${encodeURIComponent(query)}`, {
    method: "GET",
  });
}

export async function listProjectMembers(projectId: string) {
  return request<MemberResponse[]>(`/projects/${projectId}/members`, {
    method: "GET",
  });
}

export async function addProjectMember(projectId: string, email: string) {
  return request<MemberResponse>(`/projects/${projectId}/members?email=${encodeURIComponent(email)}`, {
    method: "POST",
  });
}

export async function removeProjectMember(projectId: string, memberId: string) {
  return request<{ status: string }>(`/projects/${projectId}/members/${memberId}`, {
    method: "DELETE",
  });
}

export async function transferProjectOwnership(projectId: string, memberId: string) {
  return request<{ status: string }>(`/projects/${projectId}/members/${memberId}/transfer`, {
    method: "POST",
  });
}

export async function getProjectCredentials(projectId: string) {
  return request<ProjectCredential[] | { error: string }>(`/projects/${projectId}/credentials`, {
    method: "GET",
  });
}

export async function markProjectVerified(projectId: string, username: string) {
  return request<{ status: string }>(`/projects/${projectId}/mark-verified`, {
    method: "POST",
    body: { username },
  });
}

export async function runProjectPlaywright(projectId: string, cred: ProjectCredential) {
  return request<{ status: string }>(`/projects/${projectId}/run-playwright`, {
    method: "POST",
    body: cred,
  });
}

export async function connectProjectToJira(projectId: string) {
  return request<JiraConfig>(`/projects/${projectId}/jira/connect`, {
    method: "POST",
  });
}

export async function getProjectJiraConfig(projectId: string) {
  return request<JiraConfig>(`/projects/${projectId}/jira/config`, {
    method: "GET",
  });
}

export async function startProjectPdfExtraction(projectId: string) {
  return request<{ status: string }>(`/projects/${projectId}/extract-pdfs`, {
    method: "POST",
  });
}

export async function getProjectExtractStatus(projectId: string) {
  return request<{ status: string; progress: number; logs: string[] }>(
    `/projects/${projectId}/extract-status`,
    {
      method: "GET",
    },
  );
}

// ─── Scenario API (HLS generators — main branch) ──────────────────────────

export async function generateHighLevelScenarios(projectId: string, settings: ScenarioGenerationSettings) {
  return request<{ scenarios: PreviewScenario[] }>(`/projects/${projectId}/scenarios/generate`, {
    method: "POST",
    body: settings,
  });
}

export async function approveHighLevelScenarios(projectId: string, scenarios: PreviewScenario[]) {
  return request<{ saved: number }>(`/projects/${projectId}/scenarios/approve`, {
    method: "POST",
    body: { scenarios },
  });
}

export async function listHighLevelScenarios(projectId: string) {
  return request<ScenarioListResponse>(`/projects/${projectId}/scenarios`, {
    method: "GET",
  });
}

export async function createHighLevelScenario(
  projectId: string,
  payload: { title: string; description: string },
) {
  return request<HighLevelScenario>(`/projects/${projectId}/scenarios`, {
    method: "POST",
    body: payload,
  });
}

export async function updateHighLevelScenario(
  projectId: string,
  scenarioId: string,
  payload: {
    title?: string;
    description?: string;
    status?: ScenarioStatus;
    current_user_id?: string;
  },
) {
  return request<HighLevelScenario>(`/projects/${projectId}/scenarios/${scenarioId}`, {
    method: "PATCH",
    body: payload,
  });
}

export async function deleteHighLevelScenario(projectId: string, scenarioId: string) {
  return request<{ deleted: true }>(`/projects/${projectId}/scenarios/${scenarioId}`, {
    method: "DELETE",
  });
}

// ─── Trigger (ui-discovery — polling architecture) ─────────────────────────

export async function triggerScenarioLaunch(projectId: string, scenarioId: string) {
  return request<TriggerScenarioResponse>(
    `/projects/${projectId}/scenarios/${scenarioId}/trigger`,
    {
      method: "POST",
    },
  );
}

// ─── Recording setup (ui-discovery) ───────────────────────────────────────

export async function getRecordingSetup(projectId: string): Promise<RecordingSetupResponse> {
  return request<RecordingSetupResponse>(`/projects/${projectId}/scenarios/recording-setup`, {
    method: "GET",
  });
}
