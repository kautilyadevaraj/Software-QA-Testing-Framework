export type ProjectStatus = "Active" | "Draft" | "Blocked";

export const DOCUMENT_CATEGORIES = ["BRD", "FSD", "WBS", "SwaggerDocs", "Credentials", "Assumptions"] as const;
export type DocumentCategory = (typeof DOCUMENT_CATEGORIES)[number];

export const REQUIRED_DOCUMENT_CATEGORIES: DocumentCategory[] = ["BRD", "SwaggerDocs", "Credentials"];

export type ProjectDocuments = Record<DocumentCategory, string[]>;

export function createEmptyDocuments(): ProjectDocuments {
  return {
    BRD: [],
    FSD: [],
    WBS: [],
    SwaggerDocs: [],
    Credentials: [],
    Assumptions: [],
  };
}

export type ProjectRecord = {
  id: string;
  name: string;
  description: string;
  createdAt: string;
  version: string;
  status: ProjectStatus;
  testers: string[];
  url: string;
  documents: ProjectDocuments;
};

const PROJECTS_STORAGE_KEY = "sqat-projects-v1";

const DEFAULT_PROJECTS: ProjectRecord[] = [
  {
    id: "PRJ-1001",
    name: "Checkout Regression",
    description: "Core checkout and payment workflow regression coverage.",
    createdAt: "2026-03-28T09:40:00.000Z",
    version: "v1.0.0",
    status: "Active",
    testers: ["alex@sqat.dev", "priya@sqat.dev"],
    url: "https://checkout.example.com",
    documents: {
      BRD: ["BRD_v1.pdf"],
      FSD: [],
      WBS: [],
      SwaggerDocs: ["Swagger_Checkout.yaml"],
      Credentials: ["Credentials_Checkout.pdf"],
      Assumptions: [],
    },
  },
  {
    id: "PRJ-1002",
    name: "Auth Hardening",
    description: "Authentication edge-case scenarios and security hardening checks.",
    createdAt: "2026-03-31T14:15:00.000Z",
    version: "v0.9.2",
    status: "Draft",
    testers: ["nina@sqat.dev"],
    url: "https://auth.example.com",
    documents: {
      BRD: [],
      FSD: ["Auth_FSD.pdf"],
      WBS: [],
      SwaggerDocs: [],
      Credentials: [],
      Assumptions: [],
    },
  },
];

function toSafeString(value: unknown) {
  return typeof value === "string" ? value : "";
}

function toStringArray(value: unknown) {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.filter((item): item is string => typeof item === "string");
}

function normalizeStatus(value: unknown): ProjectStatus {
  return value === "Active" || value === "Draft" || value === "Blocked" ? value : "Draft";
}

function classifyLegacyDocument(documentName: string): DocumentCategory {
  const lower = documentName.toLowerCase();
  if (lower.includes("swagger")) {
    return "SwaggerDocs";
  }
  if (lower.includes("credential") || lower.includes("cred")) {
    return "Credentials";
  }
  if (lower.includes("brd")) {
    return "BRD";
  }
  if (lower.includes("fsd")) {
    return "FSD";
  }
  if (lower.includes("wbs") || lower.includes("wsb")) {
    return "WBS";
  }
  return "Assumptions";
}

function normalizeDocuments(value: unknown): ProjectDocuments {
  const empty = createEmptyDocuments();

  if (Array.isArray(value)) {
    const legacyDocuments = value.filter((item): item is string => typeof item === "string");
    for (const documentName of legacyDocuments) {
      const category = classifyLegacyDocument(documentName);
      empty[category].push(documentName);
    }
    return empty;
  }

  if (!value || typeof value !== "object") {
    return empty;
  }

  const candidate = value as Partial<ProjectDocuments> & { WSB?: unknown };
  for (const category of DOCUMENT_CATEGORIES) {
    if (category === "WBS") {
      empty[category] = toStringArray(candidate.WBS ?? candidate.WSB);
      continue;
    }

    empty[category] = toStringArray(candidate[category]);
  }

  return empty;
}

function normalizeProject(value: unknown): ProjectRecord | null {
  if (!value || typeof value !== "object") {
    return null;
  }

  const candidate = value as Partial<ProjectRecord>;
  const id = toSafeString(candidate.id).trim();
  const name = toSafeString(candidate.name).trim();

  if (!id || !name) {
    return null;
  }

  const createdAt = toSafeString(candidate.createdAt) || new Date().toISOString();

  return {
    id,
    name,
    description: toSafeString(candidate.description),
    createdAt,
    version: toSafeString(candidate.version) || "v1.0.0",
    status: normalizeStatus(candidate.status),
    testers: toStringArray(candidate.testers),
    url: toSafeString(candidate.url),
    documents: normalizeDocuments(candidate.documents),
  };
}

function copyProjects(projects: ProjectRecord[]) {
  return projects.map((project) => ({
    ...project,
    testers: [...project.testers],
    documents: {
      BRD: [...project.documents.BRD],
      FSD: [...project.documents.FSD],
      WBS: [...project.documents.WBS],
      SwaggerDocs: [...project.documents.SwaggerDocs],
      Credentials: [...project.documents.Credentials],
      Assumptions: [...project.documents.Assumptions],
    },
  }));
}

export function getDefaultProjects() {
  return copyProjects(DEFAULT_PROJECTS);
}

export function loadProjectsFromStorage() {
  if (typeof window === "undefined") {
    return getDefaultProjects();
  }

  const raw = window.localStorage.getItem(PROJECTS_STORAGE_KEY);
  if (!raw) {
    return getDefaultProjects();
  }

  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return getDefaultProjects();
    }

    const normalized = parsed
      .map((item) => normalizeProject(item))
      .filter((item): item is ProjectRecord => item !== null);

    return normalized.length > 0 ? normalized : getDefaultProjects();
  } catch {
    return getDefaultProjects();
  }
}

export function saveProjectsToStorage(projects: ProjectRecord[]) {
  if (typeof window === "undefined") {
    return;
  }

  window.localStorage.setItem(PROJECTS_STORAGE_KEY, JSON.stringify(projects));
}
