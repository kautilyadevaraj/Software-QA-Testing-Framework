const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

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

  /** CredentialProfile UUID when the credential is a DB profile; absent for
   *  CSV-only fallback rows. Required for POST /mark-verified to toggle the
   *  correct table (see markProjectVerified). */
  id?: string;

  username: string;

  role: string | null;

  auth_type: string | null;

  auth_strategy?: string | null;

  auth_script?: string | null;

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

  recording_status: RecordingStatus | null;

  recording_step_count: number;

  recording_phase3_ready: boolean | null;

  recording_quality_failure_reasons: string[];

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



export async function markProjectVerified(
  projectId: string,
  username: string,
  credentialId?: string,
) {

  // Include credential_id whenever available so the server toggles
  // CredentialProfile.is_verified (the field the GET /credentials endpoint
  // actually returns). Without it the server silently updates a different
  // table and the UI appears unresponsive.
  return request<{ status: string }>(`/projects/${projectId}/mark-verified`, {

    method: "POST",

    body: credentialId ? { username, credential_id: credentialId } : { username },

  });

}



export async function runProjectPlaywright(projectId: string, cred: ProjectCredential) {
  return request<{ 
    status: string;
    mode?: "server_side" | "client_side";
    url?: string;
    username?: string;
    password?: string;
    role?: string;
    auth_type?: string;
  }>(`/projects/${projectId}/run-playwright`, {
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



export async function generateHighLevelScenariosStream(

  projectId: string,

  settings: ScenarioGenerationSettings,

  onProgress: (msg: string) => void

): Promise<{ scenarios: PreviewScenario[] }> {

  const response = await fetch(`${API_ROOT}/projects/${projectId}/scenarios/generate`, {

    method: "POST",

    headers: {

      "Content-Type": "application/json",

    },

    body: JSON.stringify(settings),

    credentials: "include",

  });



  if (!response.ok) {

    let errorMessage = "Failed to generate scenarios";

    try {

      const errorPayload = await response.json();

      errorMessage = getErrorMessage(errorPayload, errorMessage);

    } catch {

      // ignore

    }

    throw new ApiError(errorMessage, response.status);

  }



  if (!response.body) {

    throw new Error("No response body returned from streaming endpoint");

  }



  const reader = response.body.getReader();

  const decoder = new TextDecoder();

  let buffer = "";

  let finalScenarios: PreviewScenario[] = [];



  while (true) {

    const { done, value } = await reader.read();

    if (done) break;

    

    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split("\n");

    buffer = lines.pop() || "";



    for (const line of lines) {

      if (!line.trim()) continue;

      try {

        const chunk = JSON.parse(line);

        if (chunk.type === "progress") {

          onProgress(chunk.message);

        } else if (chunk.type === "complete") {

          finalScenarios = chunk.scenarios;

        } else if (chunk.type === "error") {

          throw new Error(chunk.message);

        }

      } catch (err) {

        if (err instanceof Error && err.message !== "Unexpected end of JSON input") {

          throw err;

        }

      }

    }

  }



  return { scenarios: finalScenarios };

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



export async function getScenarioRecordingStatus(

  projectId: string,

  scenarioId: string,

): Promise<{
  session_status: "none" | RecordingStatus;
  phase3_ready: boolean | null;
  step_count: number;
  quality_failure_reasons: string[];
}> {

  return request(`/projects/${projectId}/scenarios/${scenarioId}/recording-status`, {

    method: "GET",

  });

}



export async function stopScenarioRecording(

  projectId: string,

  scenarioId: string,

): Promise<{ status: string }> {

  return request(`/projects/${projectId}/scenarios/${scenarioId}/stop-recording`, {

    method: "POST",

  });

}

export async function clearScenarioRecording(

  projectId: string,

  scenarioId: string,

): Promise<{
  cleared: boolean;
  sessions_deleted: number;
  steps_deleted: number;
  route_variants_deleted: number;
  routes_deleted: number;
  files_deleted: number;
}> {

  return request(`/projects/${projectId}/scenarios/${scenarioId}/recording`, {

    method: "DELETE",

  });

}



// ─── Phase 3 ───────────────────────────────────────────────────────────────



export type Phase3RunStatus = {

  run_id: string;

  project_id: string;

  total: number;

  passed: number;

  failed: number;

  skipped: number;

  human_review: number;

  duration_seconds: number | null;

  status: string;

  /** 'plan' = A3 planning run | 'execute' = full Playwright run */
  run_type: "plan" | "execute";

  created_at: string;

  /** Live in-memory progress published by the graph (null when run is finished). */
  progress?: Phase3RunProgress | null;

};



export type Phase3RunProgress = {

  run_id: string;

  /** Stage tag — see server/app/services/phase3_progress.py for full set. */
  stage:
    | "planning"
    | "preflight"
    | "building_context"
    | "generating_script"
    | "queuing"
    | "running_tests"
    | "done";

  message: string;

  current_hls_index: number | null;

  total_hls: number | null;

  current_hls_title: string;

  current_test_id: string | null;

  current_test_title: string;

  /** One-line summary the UI can render verbatim. */
  headline: string;

  updated_at: number;

};



export type Phase3ReviewItem = {

  id: string;

  test_id: string;

  run_id: string;

  review_type: "BUG" | "TASK";

  evidence: Record<string, unknown>;

  status: string;

  jira_ref: string | null;

  created_at: string;

};



export async function triggerPhase3Run(projectId: string): Promise<{ run_id: string; status: string }> {

  return request(`/projects/${projectId}/phase3/trigger`, { method: "POST" });

}



export async function getPhase3RunStatus(projectId: string, runId?: string): Promise<Phase3RunStatus> {
  const qs = runId ? `?run_id=${encodeURIComponent(runId)}` : "";

  return request(`/projects/${projectId}/phase3/run-status${qs}`);

}

export type Phase3RunSummary = {
  run_id: string;
  run_type: "plan" | "execute";
  status: string;
  total: number;
  passed: number;
  failed: number;
  human_review: number;
  duration_seconds: number | null;
  created_at: string | null;
};

/** Recent Phase 3 runs for a project, newest first. Drives the history dropdown. */
export async function listPhase3Runs(
  projectId: string,
  runType: "plan" | "execute" | "all" = "all",
  limit = 20,
): Promise<Phase3RunSummary[]> {
  const params = new URLSearchParams({ run_type: runType, limit: String(limit) });
  return request(`/projects/${projectId}/phase3/runs?${params.toString()}`);
}



export async function listPhase3ReviewQueue(

  projectId: string,

  itemStatus?: string,
  runId?: string,

): Promise<Phase3ReviewItem[]> {

  const params = new URLSearchParams();
  if (itemStatus) params.set("item_status", itemStatus);
  if (runId) params.set("run_id", runId);
  const qs = params.toString() ? `?${params.toString()}` : "";

  return request(`/projects/${projectId}/phase3/review-queue${qs}`);

}



export async function patchPhase3ReviewItem(

  projectId: string,

  itemId: string,

  payload: { jira_ref?: string; status?: string },

): Promise<Phase3ReviewItem> {

  return request(`/projects/${projectId}/phase3/review-queue/${itemId}`, {

    method: "PATCH",

    body: payload,

  });

}



export async function getPhase3Script(

  projectId: string,

  testId: string,

): Promise<{ test_id: string; script_content: string }> {

  return request(`/projects/${projectId}/phase3/script/${testId}`);

}



export async function rerunPhase3ReviewItem(

  projectId: string,

  itemId: string,

  scriptContent: string,

): Promise<Phase3ReviewItem> {

  return request(`/projects/${projectId}/phase3/review-queue/${itemId}/rerun`, {

    method: "POST",

    body: { script_content: scriptContent },

  });

}



export async function raisePhase3JiraIssue(

  projectId: string,

  payload: {

    review_queue_id: string;

    issue_type: "Bug" | "Task";

    summary: string;

    description?: string;

  },

): Promise<Phase3ReviewItem> {

  return request(`/projects/${projectId}/phase3/raise-jira`, {

    method: "POST",

    body: payload,

  });

}


export type Phase3TestState = {
  test_id: string;
  tc_number?: string | null;
  title: string;
  scenario_title?: string | null;
  target_page: string;
  status: "PENDING" | "PASS" | "FAIL" | "SCRIPT_ERROR" | "APP_ERROR" | "BLOCKED" | "HUMAN_REVIEW";
  retries: number;
  blocked_by: string | null;
  network_logs_count: number;
  failure_reason?: string | null;
  review_category?: string | null;
  review_type?: "BUG" | "TASK" | null;
  review_status?: string | null;
  jira_ref?: string | null;
  trace_path?: string | null;
};

export async function getPhase3ExecutionState(projectId: string, runId?: string): Promise<Phase3TestState[]> {
  const qs = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
  return request(`/projects/${projectId}/phase3/execution-state${qs}`);
}

export type Phase3NetworkLog = {
  id: string;
  url: string;
  method: string;
  status_code: number;
  is_failure: boolean;
  created_at: string | null;
};

/** Failing 4xx/5xx network requests for a test result; backs the click-to-expand badge. */
export async function getPhase3NetworkLogs(
  projectId: string,
  testId: string,
  runId?: string,
  failuresOnly = true,
): Promise<Phase3NetworkLog[]> {
  const params = new URLSearchParams();
  if (runId) params.set("run_id", runId);
  if (!failuresOnly) params.set("failures_only", "false");
  const qs = params.toString() ? `?${params.toString()}` : "";
  return request(`/projects/${projectId}/phase3/network-logs/${testId}${qs}`);
}

// ─── Phase 3 — Generate → Approve → Execute ────────────────────────────────

export type TestCaseApprovalStatus = "PENDING" | "APPROVED" | "NEEDS_EDIT" | "EXCLUDED";

export type Phase3TestCase = {
  test_id: string;
  tc_number: string | null;
  title: string;
  steps: string[];
  acceptance_criteria: string[];
  target_page: string;
  hls_id: string | null;
  scenario_title: string | null;
  approval_status: TestCaseApprovalStatus;
  depends_on_titles: string[];
  auth_mode?: string | null;
  credential_id?: string | null;
  credential_role?: string | null;
  credential_username?: string | null;
  credential_endpoint?: string | null;
};

export type PlanRunResponse = {
  run_id: string;
  status: string;       // "planning"
  total_test_cases: number;
};

export type ApproveAllResponse = {
  approved_count: number;
};

/** Step 1: Run A3 planning for all completed HLS. Returns run_id. */
export async function planPhase3Run(projectId: string): Promise<PlanRunResponse> {
  return request(`/projects/${projectId}/phase3/plan`, { method: "POST" });
}

/** Step 3: Start Playwright execution (requires all TCs approved). */
export async function executePhase3Run(
  projectId: string,
  runId: string,
): Promise<{ run_id: string; status: string }> {
  return request(`/projects/${projectId}/phase3/execute`, {
    method: "POST",
    body: { run_id: runId },
  });
}

/** Poll after /plan completes to populate the approval accordion. */
export async function getPhase3TcDocumentJson(
  projectId: string,
  runId: string,
): Promise<Phase3TestCase[]> {
  return request(`/projects/${projectId}/phase3/tc-document/json?run_id=${runId}`);
}

/** Download X-Ray CSV test case document as a file. */
export function getPhase3TcDocumentUrl(projectId: string, runId: string): string {
  return `${(process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "")}/api/v1/projects/${projectId}/phase3/tc-document?run_id=${runId}`;
}

/** Download final execution results report as a CSV file. */
export function getPhase3ExecutionReportCsvUrl(projectId: string, runId?: string | null): string {
  const qs = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
  return `${(process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "")}/api/v1/projects/${projectId}/phase3/execution-report.csv${qs}`;
}

/** Step 2a: Bulk-approve all PENDING test cases. */
export async function approveAllPhase3TestCases(
  projectId: string,
  runId: string,
): Promise<ApproveAllResponse> {
  return request(`/projects/${projectId}/phase3/approve-all`, {
    method: "PATCH",
    body: { run_id: runId },
  });
}

/** Step 2b: Set approval status on a single test case. */
export async function setPhase3TestCaseApproval(
  projectId: string,
  testId: string,
  approvalStatus: "APPROVED" | "NEEDS_EDIT" | "EXCLUDED",
): Promise<Phase3TestCase> {
  return request(`/projects/${projectId}/phase3/test-cases/${testId}/approval`, {
    method: "PATCH",
    body: { status: approvalStatus },
  });
}

/** Inline edit: update title, steps, acceptance_criteria. Resets approval to NEEDS_EDIT. */
export async function updatePhase3TestCase(
  projectId: string,
  testId: string,
  payload: { title?: string; steps?: string[]; acceptance_criteria?: string[] },
): Promise<Phase3TestCase> {
  return request(`/projects/${projectId}/phase3/test-cases/${testId}/content`, {
    method: "PATCH",
    body: payload,
  });
}

/** Reset Phase 3 data. scope='current_run' wipes only the latest run; 'all' wipes everything for the project. */
export async function resetPhase3(
  projectId: string,
  scope: "current_run" | "all" = "all",
): Promise<{
  scope: "current_run" | "all";
  run_id?: string;
  deleted_test_cases: number;
  deleted_test_results: number;
  deleted_review_items: number;
  deleted_runs: number;
}> {
  return request(`/projects/${projectId}/phase3/reset?scope=${scope}`, {
    method: "DELETE",
  });
}

/** Cancel an active Phase 3 run — purges RabbitMQ queue and marks run as cancelled. */
export async function cancelPhase3Run(projectId: string): Promise<{
  cancelled: boolean;
  run_id: string | null;
  message: string;
}> {
  return request(`/projects/${projectId}/phase3/cancel`, { method: "POST" });
}
