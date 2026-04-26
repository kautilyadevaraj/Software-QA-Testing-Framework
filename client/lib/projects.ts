import type { DocumentResponse, ProjectResponse } from "@/lib/api";

export type ProjectStatus = "Active" | "Draft" | "Blocked";

export const DOCUMENT_CATEGORIES = ["BRD", "FSD", "WBS", "SwaggerDocs", "Credentials", "Assumptions"] as const;
export type DocumentCategory = (typeof DOCUMENT_CATEGORIES)[number];

/** These categories only allow ONE file at a time. Must delete existing before uploading a new one. */
export const SINGLE_UPLOAD_CATEGORIES: DocumentCategory[] = ["SwaggerDocs", "Credentials", "Assumptions"];

/** These categories allow multiple files. */
export const MULTI_UPLOAD_CATEGORIES: DocumentCategory[] = ["BRD", "FSD", "WBS"];

export const REQUIRED_DOCUMENT_CATEGORIES: DocumentCategory[] = ["BRD", "SwaggerDocs", "Credentials"];

export type ProjectDocumentRecord = {
  id: string;
  name: string;
  contentType: string;
  sizeBytes: number;
  createdAt: string;
};

export type ProjectDocuments = Record<DocumentCategory, ProjectDocumentRecord[]>;

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
  ownerId: string;
  name: string;
  description: string;
  createdAt: string;
  updatedAt: string;
  status: ProjectStatus;
  url: string | null;
  is_verified: boolean;
  testerIds: string[];
  testerEmails: string[];
};

export function mapProjectFromApi(project: ProjectResponse): ProjectRecord {
  return {
    id: project.id,
    ownerId: project.owner_id,
    name: project.name,
    description: project.description,
    createdAt: project.created_at,
    updatedAt: project.updated_at,
    status: project.status,
    url: project.url,
    is_verified: project.is_verified,
    testerIds: project.tester_ids ?? [],
    testerEmails: project.tester_emails ?? [],
  };
}

export function mapDocumentsFromApi(items: DocumentResponse[]): ProjectDocuments {
  const grouped = createEmptyDocuments();

  for (const item of items) {
    grouped[item.category].push({
      id: item.id,
      name: item.original_filename,
      contentType: item.content_type,
      sizeBytes: item.size_bytes,
      createdAt: item.created_at,
    });
  }

  return grouped;
}
