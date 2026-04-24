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

async function request<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
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
    const message = getErrorMessage(payload as ApiErrorPayload | null, `Request failed with ${response.status}`);
    throw new ApiError(message, response.status);
  }

  return payload as T;
}

export type AuthUser = {
  id: string;
  email: string;
  role: string;
  created_at: string;
};

export type AuthResponse = {
  user: AuthUser;
};

export type ProjectStatus = "Active" | "Draft" | "Blocked";

export type ProjectResponse = {
  id: string;
  name: string;
  description: string;
  status: ProjectStatus;
  url: string | null;
  created_at: string;
  updated_at: string;
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

export async function createTicket(projectId: string, title: string, description: string) {
  return request<{ id: string; project_id: string; title: string; description: string; status: string; created_at: string }>(
    `/projects/${projectId}/tickets`,
    {
      method: "POST",
      body: { title, description },
    }
  );
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

// ---------------------------------------------------------------------------
// Ingestion / Knowledge Base
// ---------------------------------------------------------------------------

export type IngestionJobResponse = {
  id: string;
  project_id: string;
  status: "pending" | "processing" | "completed" | "failed";
  total_files: number;
  processed_files: number;
  total_chunks: number;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
};

export type IngestionStatusResponse = {
  job: IngestionJobResponse | null;
  total_chunks: number;
  total_endpoints: number;
};

export type ApiEndpointResponse = {
  id: string;
  http_method: string;
  path: string;
  operation_id: string | null;
  summary: string;
  description: string;
  tags: string[];
  created_at: string;
};

export type ApiEndpointListResponse = {
  items: ApiEndpointResponse[];
  total: number;
};

export type DocumentChunkResponse = {
  id: string;
  file_id: string;
  chunk_index: number;
  content: string;
  token_count: number;
  page_number: number | null;
  source_type: string;
  chunk_metadata: Record<string, unknown>;
  created_at: string;
};

export type DocumentChunkListResponse = {
  items: DocumentChunkResponse[];
  total: number;
  page: number;
  page_size: number;
};

export async function getIngestionStatus(projectId: string) {
  return request<IngestionStatusResponse>(`/projects/${projectId}/ingest/status`, {
    method: "GET",
  });
}

export async function listApiEndpoints(projectId: string) {
  return request<ApiEndpointListResponse>(`/projects/${projectId}/ingest/endpoints`, {
    method: "GET",
  });
}

export async function listDocumentChunks(projectId: string, page = 1, pageSize = 20) {
  const query = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  return request<DocumentChunkListResponse>(`/projects/${projectId}/ingest/chunks?${query.toString()}`, {
    method: "GET",
  });
}
