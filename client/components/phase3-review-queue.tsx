"use client";

import { useEffect, useRef, useState } from "react";
import { Bug, CheckCircle2, ExternalLink, Loader2, Pencil, RotateCcw, Ticket } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  ApiError,
  listPhase3ReviewQueue,
  patchPhase3ReviewItem,
  raisePhase3JiraIssue,
  type Phase3ReviewItem,
} from "@/lib/api";
import { Phase3ScriptEditorModal } from "@/components/phase3-script-editor-modal";

const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

type Props = {
  projectId: string;
  active: boolean;
};

function formatDate(iso: string) {
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(iso));
}

export function Phase3ReviewQueue({ projectId, active }: Props) {
  const [items, setItems] = useState<Phase3ReviewItem[]>([]);
  const [editItem, setEditItem] = useState<Phase3ReviewItem | null>(null);
  const [jiraItem, setJiraItem] = useState<Phase3ReviewItem | null>(null);
  const [jiraSummary, setJiraSummary] = useState("");
  const [jiraSubmitting, setJiraSubmitting] = useState(false);
  const [markingId, setMarkingId] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  // ── Initial load — fetch existing items from DB ─────────────────────────
  useEffect(() => {
    listPhase3ReviewQueue(projectId)
      .then((data) => setItems(data))
      .catch(() => {/* ignore */ });
  }, [projectId]);

  // ── Poll for status updates while any item is 'rerunning' ───────────────
  useEffect(() => {
    const hasRerunning = items.some((i) => i.status === "rerunning");
    if (!hasRerunning) return;
    const id = setInterval(async () => {
      try {
        const fresh = await listPhase3ReviewQueue(projectId);
        setItems(fresh);
      } catch {
        // ignore transient errors
      }
    }, 3000);
    return () => clearInterval(id);
  }, [items, projectId]);

  // ── SSE connection — real-time additions during active run ──────────────
  useEffect(() => {
    if (!active) {
      esRef.current?.close();
      esRef.current = null;
      return;
    }

    const url = `${API_BASE_URL}/api/v1/projects/${projectId}/phase3/review-queue/stream`;
    const es = new EventSource(url, { withCredentials: true });
    esRef.current = es;

    es.addEventListener("review_item", (ev) => {
      try {
        const item: Phase3ReviewItem = JSON.parse(ev.data);
        setItems((prev) => {
          if (prev.some((i) => i.id === item.id)) return prev;
          return [item, ...prev];
        });
      } catch {
        // malformed event — ignore
      }
    });

    es.onerror = () => {
      es.close();
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [active, projectId]);

  // ── Handlers ────────────────────────────────────────────────────────────

  function onRerun(updated: Phase3ReviewItem) {
    setItems((prev) => prev.map((i) => (i.id === updated.id ? updated : i)));
  }

  async function handleMarkReviewed(item: Phase3ReviewItem) {
    setMarkingId(item.id);
    try {
      const updated = await patchPhase3ReviewItem(projectId, item.id, { status: "reviewed" });
      setItems((prev) => prev.map((i) => (i.id === updated.id ? updated : i)));
      toast.success("Marked as reviewed");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to update");
    } finally {
      setMarkingId(null);
    }
  }

  async function handleRaiseJira() {
    if (!jiraItem) return;
    setJiraSubmitting(true);
    try {
      const updated = await raisePhase3JiraIssue(projectId, {
        review_queue_id: jiraItem.id,
        issue_type: jiraItem.review_type === "BUG" ? "Bug" : "Task",
        summary: jiraSummary || `[Phase 3] ${jiraItem.review_type}: ${jiraItem.test_id.slice(0, 8)}`,
        description: JSON.stringify(jiraItem.evidence, null, 2),
      });
      setItems((prev) => prev.map((i) => (i.id === updated.id ? updated : i)));
      toast.success(`Jira issue raised: ${updated.jira_ref}`);
      setJiraItem(null);
      setJiraSummary("");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to raise Jira issue");
    } finally {
      setJiraSubmitting(false);
    }
  }

  // ── Render ───────────────────────────────────────────────────────────────

  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-gray-200 py-16 text-center">
        <CheckCircle2 className="mb-3 h-10 w-10 text-gray-300" />
        <p className="text-sm font-medium text-gray-500">No items in review queue</p>
        <p className="mt-1 text-xs text-gray-400">Items appear here in real-time when tests fail</p>
      </div>
    );
  }

  return (
    <>
      <div className="space-y-3">
        {items.map((item) => {
          const evidence = item.evidence as Record<string, unknown>;
          const failingRequests = Array.isArray(evidence.failing_requests)
            ? (evidence.failing_requests as { url: string; method: string; status: number }[])
            : [];

          return (
            <div
              key={item.id}
              className={`rounded-xl border p-4 transition-colors ${item.status === "reviewed"
                  ? "border-gray-100 bg-gray-50 opacity-70"
                  : item.review_type === "BUG"
                    ? "border-red-100 bg-red-50"
                    : "border-amber-100 bg-amber-50"
                }`}
            >
              <div className="flex items-start justify-between gap-4">
                {/* Left: badge + test id + evidence */}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span
                      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ${item.review_type === "BUG"
                          ? "bg-red-100 text-red-700"
                          : "bg-amber-100 text-amber-700"
                        }`}
                    >
                      {item.review_type === "BUG" ? (
                        <Bug className="h-3 w-3" />
                      ) : (
                        <RotateCcw className="h-3 w-3" />
                      )}
                      {item.review_type}
                    </span>
                    <span className="rounded bg-gray-200 px-1.5 py-0.5 font-mono text-xs text-gray-600">
                      {item.test_id.slice(0, 8)}
                    </span>
                    {item.jira_ref && (
                      <span className="text-xs font-medium text-blue-600">{item.jira_ref}</span>
                    )}
                    <span
                      className={`ml-auto text-xs font-medium ${item.status === "reviewed"
                          ? "text-green-600"
                          : item.status === "rerunning"
                            ? "text-blue-600"
                            : "text-gray-500"
                        }`}
                    >
                      {item.status}
                    </span>
                  </div>

                  {failingRequests.length > 0 && (
                    <div className="mt-2 space-y-1">
                      {failingRequests.slice(0, 3).map((req, idx) => (
                        <div key={idx} className="flex items-center gap-2 text-xs text-gray-600">
                          <span className="rounded bg-red-100 px-1 font-mono text-red-700">
                            {req.status}
                          </span>
                          <span className="font-mono font-medium uppercase text-gray-500">
                            {req.method}
                          </span>
                          <span className="truncate">{req.url}</span>
                        </div>
                      ))}
                    </div>
                  )}

                  {typeof evidence.error_log === "string" && evidence.error_log && (
                    <p className="mt-2 line-clamp-2 rounded bg-gray-100 px-2 py-1 font-mono text-xs text-gray-500">
                      {evidence.error_log}
                    </p>
                  )}

                  <p className="mt-2 text-xs text-gray-400">{formatDate(item.created_at)}</p>
                </div>

                {/* Right: actions */}
                {item.status !== "reviewed" && (
                  <div className="flex shrink-0 flex-col gap-1.5">
                    <Button
                      size="sm"
                      variant="outline"
                      className="gap-1.5 text-xs"
                      onClick={() => setEditItem(item)}
                    >
                      <Pencil className="h-3 w-3" />
                      Edit &amp; Re-run
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      className="gap-1.5 text-xs"
                      onClick={() => {
                        setJiraItem(item);
                        setJiraSummary(`[Phase 3] ${item.review_type}: ${item.test_id.slice(0, 8)}`);
                      }}
                    >
                      <Ticket className="h-3 w-3" />
                      Raise JIRA
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      className="gap-1.5 text-xs"
                      disabled={markingId === item.id}
                      onClick={() => handleMarkReviewed(item)}
                    >
                      {markingId === item.id ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <CheckCircle2 className="h-3 w-3" />
                      )}
                      Mark Reviewed
                    </Button>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Script Editor Modal */}
      {editItem && (
        <Phase3ScriptEditorModal
          open={!!editItem}
          onClose={() => setEditItem(null)}
          projectId={projectId}
          item={editItem}
          onRerun={onRerun}
        />
      )}

      {/* Raise JIRA inline mini-modal */}
      {jiraItem && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-2xl">
            <h3 className="mb-4 font-semibold text-gray-900">Raise JIRA Issue</h3>
            <div className="space-y-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-gray-600">Summary</label>
                <input
                  type="text"
                  value={jiraSummary}
                  onChange={(e) => setJiraSummary(e.target.value)}
                  className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div className="rounded-lg bg-gray-50 p-3 text-xs text-gray-500">
                <span className="font-medium">Type: </span>
                {jiraItem.review_type === "BUG" ? "Bug" : "Task"}
                {" · "}
                <span className="font-medium">Test: </span>
                {jiraItem.test_id.slice(0, 8)}
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => { setJiraItem(null); setJiraSummary(""); }}
                disabled={jiraSubmitting}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                onClick={handleRaiseJira}
                disabled={jiraSubmitting || !jiraSummary.trim()}
                className="gap-1.5"
              >
                {jiraSubmitting ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <ExternalLink className="h-3.5 w-3.5" />
                )}
                Create Issue
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
