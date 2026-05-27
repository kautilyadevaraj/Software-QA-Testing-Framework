"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity, CheckCircle2, ChevronDown, ChevronRight,
  Download, Loader2, Pencil, Play, RefreshCw, Save, Square, Trash2, ThumbsDown, ThumbsUp, X, XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  ApiError,
  approveAllPhase3TestCases,
  cancelPhase3Run,
  executePhase3Run,
  getPhase3ExecutionState,
  getPhase3NetworkLogs,
  getPhase3RunStatus,
  getPhase3TcDocumentJson,
  getPhase3TcDocumentUrl,
  listHighLevelScenarios,
  listPhase3Runs,
  planPhase3Run,
  resetPhase3,
  setPhase3TestCaseApproval,
  updatePhase3TestCase,
  type Phase3NetworkLog,
  type Phase3RunStatus,
  type Phase3RunSummary,
  type Phase3TestCase,
  type Phase3TestState,
} from "@/lib/api";
import { Phase3ReviewQueue } from "@/components/phase3-review-queue";
import { Phase3TabBar, type Phase3Tab } from "@/components/phase3-tab-bar";

// ── Types ─────────────────────────────────────────────────────────────────────

type UiPhase = "idle" | "planning" | "review" | "executing" | "done";

type Props = { projectId: string };
type TestCaseFilter = "ALL" | "PENDING" | "APPROVED" | "NEEDS_EDIT" | "EXCLUDED";

const POLL_MS = 5000;
const EXEC_POLL_MS = 2000;
const TC_POLL_MS = 3000;

// ── Status helpers ────────────────────────────────────────────────────────────

const EXEC_STATUS: Record<Phase3TestState["status"], { label: string; dot: string }> = {
  PENDING: { label: "Pending", dot: "bg-gray-300 animate-pulse" },
  PASS: { label: "Pass", dot: "bg-green-500" },
  FAIL: { label: "Fail", dot: "bg-red-500" },
  SCRIPT_ERROR: { label: "Script Error", dot: "bg-orange-500" },
  APP_ERROR: { label: "App Error", dot: "bg-red-500" },
  BLOCKED: { label: "Blocked", dot: "bg-purple-400" },
  HUMAN_REVIEW: { label: "Review", dot: "bg-amber-500" },
};

/** Inline expand-on-click badge that fetches the failing 4xx/5xx requests for
 *  a test on demand. Keeps the live-log row terse by default but lets the
 *  demo audience see *what* failed without leaving the page (P2.8). */
function NetworkLogsBadge({
  projectId, testId, count, runId,
}: {
  projectId: string;
  testId: string;
  count: number;
  runId?: string;
}) {
  const [open, setOpen] = useState(false);
  const [logs, setLogs] = useState<Phase3NetworkLog[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (logs !== null) return; // already loaded
    setLoading(true);
    setError(null);
    try {
      const data = await getPhase3NetworkLogs(projectId, testId, runId, true);
      setLogs(data);
    } catch {
      setError("Failed to load network logs");
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={toggle}
        className="inline-flex items-center gap-1 rounded-full bg-red-50 px-2 py-0.5 text-xs text-red-600 hover:bg-red-100"
        title={open ? "Hide failing requests" : "Show failing 4xx/5xx requests"}
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        {count} 4xx/5xx
      </button>
      {open && (
        <div className="mt-1 w-full rounded-md border border-red-100 bg-red-50/40 p-2 text-xs">
          {loading && <p className="text-gray-500">Loading…</p>}
          {error && <p className="text-red-600">{error}</p>}
          {!loading && !error && logs && logs.length === 0 && (
            <p className="text-gray-500">No failing requests recorded.</p>
          )}
          {!loading && !error && logs && logs.length > 0 && (
            <ul className="space-y-1 font-mono">
              {logs.map(l => (
                <li key={l.id} className="flex items-baseline gap-2">
                  <span
                    className={`shrink-0 rounded px-1 text-[10px] font-bold ${
                      l.status_code >= 500
                        ? "bg-red-200 text-red-800"
                        : "bg-amber-200 text-amber-800"
                    }`}
                  >
                    {l.status_code}
                  </span>
                  <span className="shrink-0 text-gray-500">{l.method}</span>
                  <span className="truncate text-gray-700" title={l.url}>{l.url}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </>
  );
}

function StatusBadge({ status }: { status: Phase3TestState["status"] }) {
  const cfg = EXEC_STATUS[status] ?? EXEC_STATUS.PENDING;
  const colors: Record<string, string> = {
    PASS: "bg-green-100 text-green-700", FAIL: "bg-red-100 text-red-700",
    SCRIPT_ERROR: "bg-orange-100 text-orange-700", APP_ERROR: "bg-red-100 text-red-700",
    BLOCKED: "bg-purple-100 text-purple-700", HUMAN_REVIEW: "bg-amber-100 text-amber-700",
    PENDING: "bg-gray-100 text-gray-500",
  };
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${colors[status] ?? colors.PENDING}`}>
      {cfg.label}
    </span>
  );
}

function ApprovalChip({ tc, projectId, onUpdate, disabled = false }: {
  tc: Phase3TestCase; projectId: string; onUpdate: (updated: Phase3TestCase) => void; disabled?: boolean;
}) {
  const [loading, setLoading] = useState(false);

  async function patch(s: "APPROVED" | "NEEDS_EDIT" | "EXCLUDED") {
    if (disabled) return;
    setLoading(true);
    try {
      const updated = await setPhase3TestCaseApproval(projectId, tc.test_id, s);
      onUpdate(updated);
    } catch { toast.error("Failed to update approval"); }
    finally { setLoading(false); }
  }

  if (loading) return <Loader2 className="h-4 w-4 animate-spin text-gray-400" />;

  if (tc.approval_status === "APPROVED") {
    return (
      <div className="flex items-center gap-1">
        <button onClick={() => patch("NEEDS_EDIT")} disabled={disabled}
          className="inline-flex items-center gap-1 rounded-full bg-green-100 px-2.5 py-1 text-xs font-semibold text-green-700 hover:bg-green-200 disabled:cursor-not-allowed disabled:opacity-70 transition-colors">
          <CheckCircle2 className="h-3 w-3" /> Approved
        </button>
        <button onClick={() => patch("EXCLUDED")} disabled={disabled}
          className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2.5 py-1 text-xs font-semibold text-gray-600 hover:bg-amber-100 hover:text-amber-700 disabled:cursor-not-allowed disabled:opacity-70 transition-colors">
          <Trash2 className="h-3 w-3" /> Exclude
        </button>
      </div>
    );
  }
  if (tc.approval_status === "NEEDS_EDIT") {
    return (
      <div className="flex items-center gap-1">
        <button onClick={() => patch("APPROVED")} disabled={disabled}
          className="inline-flex items-center gap-1 rounded-full bg-red-100 px-2.5 py-1 text-xs font-semibold text-red-700 hover:bg-red-200 disabled:cursor-not-allowed disabled:opacity-70 transition-colors">
          <XCircle className="h-3 w-3" /> Needs Edit
        </button>
        <button onClick={() => patch("EXCLUDED")} disabled={disabled}
          className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2.5 py-1 text-xs font-semibold text-gray-600 hover:bg-amber-100 hover:text-amber-700 disabled:cursor-not-allowed disabled:opacity-70 transition-colors">
          <Trash2 className="h-3 w-3" /> Exclude
        </button>
      </div>
    );
  }
  if (tc.approval_status === "EXCLUDED") {
    return (
      <button onClick={() => patch("APPROVED")} disabled={disabled}
        className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2.5 py-1 text-xs font-semibold text-amber-700 hover:bg-green-100 hover:text-green-700 disabled:cursor-not-allowed disabled:opacity-70 transition-colors">
        <Trash2 className="h-3 w-3" /> Excluded
      </button>
    );
  }
  return (
    <div className="flex items-center gap-1">
      <button onClick={() => patch("APPROVED")} disabled={disabled}
        className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2.5 py-1 text-xs font-semibold text-gray-600 hover:bg-green-100 hover:text-green-700 disabled:cursor-not-allowed disabled:opacity-70 transition-colors">
        <ThumbsUp className="h-3 w-3" /> Approve
      </button>
      <button onClick={() => patch("NEEDS_EDIT")} disabled={disabled}
        className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2.5 py-1 text-xs font-semibold text-gray-600 hover:bg-red-100 hover:text-red-700 disabled:cursor-not-allowed disabled:opacity-70 transition-colors">
        <ThumbsDown className="h-3 w-3" /> Needs Edit
      </button>
      <button onClick={() => patch("EXCLUDED")} disabled={disabled}
        className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2.5 py-1 text-xs font-semibold text-gray-600 hover:bg-amber-100 hover:text-amber-700 disabled:cursor-not-allowed disabled:opacity-70 transition-colors">
        <Trash2 className="h-3 w-3" /> Exclude
      </button>
    </div>
  );
}

function TcAccordion({ tc, projectId, onUpdate, executionState, readOnly = false }: {
  tc: Phase3TestCase;
  projectId: string;
  onUpdate: (u: Phase3TestCase) => void;
  executionState?: Phase3TestState;
  readOnly?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);

  // Edit-mode draft state
  const [draftTitle, setDraftTitle] = useState("");
  const [draftSteps, setDraftSteps] = useState("");
  const [draftAC, setDraftAC] = useState("");

  function startEdit() {
    if (readOnly) return;
    setDraftTitle(tc.title);
    setDraftSteps((tc.steps as string[]).join("\n"));
    setDraftAC((tc.acceptance_criteria as string[]).join("\n"));
    setEditing(true);
  }

  function cancelEdit() { setEditing(false); }

  async function saveEdit() {
    setSaving(true);
    try {
      const updated = await updatePhase3TestCase(projectId, tc.test_id, {
        title: draftTitle.trim() || tc.title,
        steps: draftSteps.split("\n").map(s => s.trim()).filter(Boolean),
        acceptance_criteria: draftAC.split("\n").map(s => s.trim()).filter(Boolean),
      });
      onUpdate(updated);
      setEditing(false);
      toast.success("Test case updated — re-approve before executing");
    } catch {
      toast.error("Failed to save changes");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="border rounded-lg overflow-hidden">
      {/* Row: plain div — expand toggle button on left, ApprovalChip on right */}
      <div className="flex items-center bg-white hover:bg-gray-50 transition-colors">
        <button
          type="button"
          onClick={() => setOpen(o => !o)}
          className="flex items-center gap-3 flex-1 min-w-0 px-4 py-3 text-left focus:outline-none"
        >
          {open
            ? <ChevronDown className="h-4 w-4 shrink-0 text-gray-400" />
            : <ChevronRight className="h-4 w-4 shrink-0 text-gray-400" />}
          <span className="text-xs font-mono text-gray-400 shrink-0">{tc.tc_number || "TC-?"}</span>
          <span className="flex-1 text-sm font-medium text-gray-800 truncate">{tc.title}</span>
          {executionState && <StatusBadge status={executionState.status} />}
          <span className="text-xs text-gray-400 shrink-0">{tc.target_page}</span>
        </button>
        {/* ApprovalChip — outside the button, no nested-button issue */}
        <div className="px-3 shrink-0">
          <ApprovalChip tc={tc} projectId={projectId} onUpdate={onUpdate} disabled={readOnly} />
        </div>
      </div>

      {open && (
        <div className="px-4 py-3 bg-gray-50 border-t space-y-3 text-sm">
          {tc.depends_on_titles.length > 0 && (
            <p className="text-xs text-gray-400">Depends on: {tc.depends_on_titles.join(", ")}</p>
          )}

          {editing ? (
            /* ── Edit mode ── */
            <div className="space-y-3">
              <div>
                <label className="text-xs font-semibold text-gray-600 block mb-1">Title</label>
                <input
                  value={draftTitle}
                  onChange={e => setDraftTitle(e.target.value)}
                  className="w-full rounded border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="text-xs font-semibold text-gray-600 block mb-1">
                  Steps <span className="font-normal text-gray-400">(one per line)</span>
                </label>
                <textarea
                  rows={Math.max(4, draftSteps.split("\n").length + 1)}
                  value={draftSteps}
                  onChange={e => setDraftSteps(e.target.value)}
                  className="w-full rounded border border-gray-300 px-3 py-1.5 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-blue-500 resize-y"
                />
              </div>
              <div>
                <label className="text-xs font-semibold text-gray-600 block mb-1">
                  Acceptance Criteria <span className="font-normal text-gray-400">(one per line)</span>
                </label>
                <textarea
                  rows={Math.max(3, draftAC.split("\n").length + 1)}
                  value={draftAC}
                  onChange={e => setDraftAC(e.target.value)}
                  className="w-full rounded border border-gray-300 px-3 py-1.5 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-blue-500 resize-y"
                />
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={saveEdit}
                  disabled={saving}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
                >
                  {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
                  Save
                </button>
                <button
                  type="button"
                  onClick={cancelEdit}
                  disabled={saving}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-100 disabled:opacity-50 transition-colors"
                >
                  <X className="h-3 w-3" /> Cancel
                </button>
              </div>
            </div>
          ) : (
            /* ── View mode ── */
            <>
              <div className="flex items-center justify-between">
                <p className="font-semibold text-gray-700">Steps</p>
                <button
                  type="button"
                  onClick={startEdit}
                  disabled={readOnly}
                  className="inline-flex items-center gap-1 text-xs text-gray-400 hover:text-blue-600 disabled:cursor-not-allowed disabled:opacity-50 transition-colors"
                >
                  <Pencil className="h-3 w-3" /> Edit
                </button>
              </div>
              <ol className="list-decimal list-inside space-y-0.5">
                {(tc.steps as string[]).map((s, i) => (
                  <li key={i} className="text-gray-600 text-xs">{s}</li>
                ))}
              </ol>
              {(tc.acceptance_criteria as string[]).length > 0 && (
                <div>
                  <p className="font-semibold text-gray-700 mb-1">Acceptance Criteria</p>
                  <ul className="space-y-0.5">
                    {(tc.acceptance_criteria as string[]).map((c, i) => (
                      <li key={i} className="text-xs text-gray-600 flex gap-2">
                        <CheckCircle2 className="h-3 w-3 mt-0.5 shrink-0 text-green-500" /> {c}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

export function Phase3Panel({ projectId }: Props) {
  const phase3TabStorageKey = `sqat:${projectId}:phase3-tab`;
  const [phase, setPhase] = useState<UiPhase>("idle");
  const [allCompleted, setAllCompleted] = useState<boolean | null>(null);
  const [planRunId, setPlanRunId] = useState<string | null>(null);
  const [executeRunId, setExecuteRunId] = useState<string | null>(null);
  const [planRuns, setPlanRuns] = useState<Phase3RunSummary[]>([]);
  const [testCases, setTestCases] = useState<Phase3TestCase[]>([]);
  const [runStatus, setRunStatus] = useState<Phase3RunStatus | null>(null);
  const [execState, setExecState] = useState<Phase3TestState[]>([]);
  const [approvingAll, setApprovingAll] = useState(false);
  const [activeTab, setActiveTab] = useState<Phase3Tab>(() => {
    if (typeof window === "undefined") return "testcases";
    const stored = window.localStorage.getItem(phase3TabStorageKey);
    return stored === "execution" || stored === "report" || stored === "testcases" ? stored : "testcases";
  });
  const [tcFilter, setTcFilter] = useState<TestCaseFilter>("ALL");
  const [tcSearch, setTcSearch] = useState("");

  // Ref mirror of testCases so callbacks can read length without stale closure
  const testCasesRef = useRef<Phase3TestCase[]>([]);
  const executeRunIdRef = useRef<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const execPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const tcPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopAll = () => {
    [pollRef, execPollRef, tcPollRef].forEach(r => { if (r.current) { clearInterval(r.current); r.current = null; } });
  };

  useEffect(() => {
    window.localStorage.setItem(phase3TabStorageKey, activeTab);
  }, [activeTab, phase3TabStorageKey]);

  // Check whether at least one Phase 2 scenario is recorded/completed.
  const checkScenarios = useCallback(async () => {
    try {
      const d = await listHighLevelScenarios(projectId);
      setAllCompleted(d.scenarios.some(s => s.status === "completed"));
    } catch { setAllCompleted(false); }
  }, [projectId]);

  // Poll run status — drives phase transitions for both on-mount detection and live polling
  const fetchRunStatus = useCallback(async () => {
    try {
      const s = await getPhase3RunStatus(projectId);
      setRunStatus(s);

      if (s.run_type === "execute") {
        setExecuteRunId(s.run_id);
        executeRunIdRef.current = s.run_id;
        if (testCasesRef.current.length === 0) {
          try {
            const plans = await listPhase3Runs(projectId, "plan", 1);
            const latestPlanId = plans[0]?.run_id;
            if (latestPlanId) {
              setPlanRunId(prev => prev ?? latestPlanId);
              const rows = await getPhase3TcDocumentJson(projectId, latestPlanId);
              setTestCases(rows);
              testCasesRef.current = rows;
            }
          } catch {
            // Non-fatal: execution log still renders, but TC traceability will
            // restore on the next successful status poll/history fetch.
          }
        }
        if (s.status === "running") {
          // Run is live — enter executing phase and start live pollers
          setPhase(prev => (prev === "executing" ? prev : "executing"));
          if (!execPollRef.current) {
            execPollRef.current = setInterval(fetchExecState, EXEC_POLL_MS);
            fetchExecState();
          }
          if (!pollRef.current) {
            pollRef.current = setInterval(fetchRunStatus, POLL_MS);
          }
        } else {
          // Completed / failed — stop pollers and show done state
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
          if (execPollRef.current) { clearInterval(execPollRef.current); execPollRef.current = null; }
          setPhase("done");
          fetchExecState(); // one final snapshot
        }
      }

      if (s.run_type === "plan" && s.status === "planned") {
        // Planning is done but execute hasn't started — show review phase.
        // Also hydrate planRunId from the run-status response so that a page
        // refresh can restore the accordion without needing a re-generate.
        const restoredRunId = s.run_id;
        setPlanRunId(prev => {
          const resolved = prev ?? restoredRunId;
          // Fetch TCs immediately if we haven't loaded them yet
          if (resolved && testCasesRef.current.length === 0) {
            fetchTcDoc(resolved);
          }
          return resolved;
        });
        if (tcPollRef.current) { clearInterval(tcPollRef.current); tcPollRef.current = null; }
        setActiveTab(prev => (prev === "execution" ? prev : "testcases"));
        setPhase(prev => (prev === "review" ? prev : "review"));
      }
    } catch { /* 404 = no run yet */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // Plan-run history (P2.9) — lets the user switch between prior plan runs
  // without losing approval/review context. Refreshed after every successful
  // plan generation and on mount so the dropdown stays current.
  const fetchPlanRuns = useCallback(async () => {
    try {
      const runs = await listPhase3Runs(projectId, "plan", 20);
      setPlanRuns(runs);
    } catch {
      // Non-fatal — dropdown just won't render until next refresh.
    }
  }, [projectId]);

  const fetchInitialRunStatus = useCallback(async () => {
    try {
      const runs = await listPhase3Runs(projectId, "all", 1);
      if (runs.length > 0) {
        await fetchRunStatus();
      }
    } catch {
      // Non-fatal: the panel can stay idle until the user starts planning.
    }
  }, [fetchRunStatus, projectId]);

  // Allow the user to jump to an earlier plan run from the history dropdown.
  function handleSelectPlanRun(runId: string) {
    if (!runId || runId === planRunId) return;
    setPlanRunId(runId);
    setTestCases([]);
    testCasesRef.current = [];
    setActiveTab("testcases");
    setPhase("review");
    fetchTcDoc(runId);
  }

  // Poll TC document during planning — stop when backend sets status to 'planned'
  const fetchTcDoc = useCallback(async (runId: string) => {
    try {
      const rows = await getPhase3TcDocumentJson(projectId, runId);
      if (rows.length > 0) {
        setTestCases(rows);
        testCasesRef.current = rows;
        setActiveTab(prev => (prev === "execution" ? prev : "testcases"));
      }
    } catch { /* not ready yet */ }
  }, [projectId]);

  // Poll exec state
  const fetchExecState = useCallback(async () => {
    try { setExecState(await getPhase3ExecutionState(projectId, executeRunIdRef.current ?? undefined)); }
    catch { /* ignore */ }
  }, [projectId]);

  useEffect(() => {
    checkScenarios();
    fetchInitialRunStatus(); // on mount: auto-detect existing runs without causing no-run 404s
    fetchPlanRuns();  // populate history dropdown
    return stopAll;
  }, [checkScenarios, fetchInitialRunStatus, fetchPlanRuns]);

  // Secondary effect for planning-complete detection via TC poll
  useEffect(() => {
    if (!runStatus) return;
    if (runStatus.run_type === "plan" && runStatus.status === "planned") {
      if (tcPollRef.current) { clearInterval(tcPollRef.current); tcPollRef.current = null; }
      setActiveTab(prev => (prev === "execution" ? prev : "testcases"));
      setPhase("review");
      // planRunId may already be set from the fetchRunStatus effect above;
      // fall back to run_id from the status response.
      const resolvedRunId = planRunId ?? runStatus.run_id;
      if (resolvedRunId && testCasesRef.current.length === 0) fetchTcDoc(resolvedRunId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runStatus?.status, runStatus?.run_type]);

  // ── Step 1: Generate ────────────────────────────────────────────────────────
  async function handleGenerate() {
    setPhase("planning");
    setActiveTab("testcases");
    setTestCases([]);
    try {
      const res = await planPhase3Run(projectId);
      setPlanRunId(res.run_id);
      // Refresh history dropdown so the new run appears immediately.
      fetchPlanRuns();
      // Poll run-status so useEffect can detect when planning transitions to 'planned'
      if (!pollRef.current) {
        pollRef.current = setInterval(fetchRunStatus, POLL_MS);
      }
      // Poll TC doc every 3s so test cases appear incrementally
      tcPollRef.current = setInterval(() => fetchTcDoc(res.run_id), TC_POLL_MS);
      fetchTcDoc(res.run_id);
    } catch (err) {
      setPhase("idle");
      toast.error(err instanceof ApiError ? err.message : "Failed to generate test cases");
    }
  }

  // ── Reset: clear Phase 3 data (current run only, or everything) ──────────
  // Two scopes prevent accidental nukes during demos — "current_run" lets the
  // user retry just the latest plan/execute without losing earlier history.
  async function handleReset(scope: "current_run" | "all") {
    const confirmMsg =
      scope === "current_run"
        ? "Clear the current run? This deletes test cases, results, and review items for the most recent run only. Earlier runs are preserved."
        : "Clear EVERYTHING? This deletes every test case, run, result, and review item for this project. This cannot be undone.";
    if (!window.confirm(confirmMsg)) return;

    try {
      stopAll();
      const res = await resetPhase3(projectId, scope);
      setPhase("idle");
      setTestCases([]);
      setRunStatus(null);
      setExecState([]);
      setPlanRunId(null);
      setExecuteRunId(null);
      executeRunIdRef.current = null;
      const label = scope === "current_run" ? "Current run cleared" : "All Phase 3 data cleared";
      toast.success(
        `${label}: ${res.deleted_test_cases} TCs, ${res.deleted_runs} runs, ${res.deleted_review_items} review items`,
      );
    } catch {
      toast.error("Reset failed — check server logs");
    }
  }

  // ── Cancel: stop active run immediately ────────────────────────────────────
  async function handleCancel() {
    try {
      stopAll(); // stop pollers immediately so UI stops updating
      const res = await cancelPhase3Run(projectId);
      setPhase("idle");
      setExecState([]);
      setExecuteRunId(null);
      executeRunIdRef.current = null;
      toast.info(res.message);
    } catch {
      toast.error("Cancel request failed — try refreshing the page");
    }
  }

  // ── Step 2a: Approve All ────────────────────────────────────────────────────
  async function handleApproveAll() {
    if (!planRunId) return;
    setApprovingAll(true);
    try {
      const res = await approveAllPhase3TestCases(projectId, planRunId);
      setTestCases(prev => prev.map(tc => (
        tc.approval_status === "EXCLUDED"
          ? tc
          : { ...tc, approval_status: "APPROVED" as const }
      )));
      toast.success(`Approved ${res.approved_count} test cases`);
    } catch { toast.error("Failed to approve all"); }
    finally { setApprovingAll(false); }
  }

  // ── Step 3: Execute ─────────────────────────────────────────────────────────
  async function handleExecute() {
    if (!planRunId) return;
    const activeCases = testCases.filter(tc => tc.approval_status !== "EXCLUDED");
    const allApproved = activeCases.length > 0 && activeCases.every(tc => tc.approval_status === "APPROVED");
    if (!allApproved) { toast.error("Approve or exclude all active test cases before executing"); return; }
    setPhase("executing");
    setActiveTab("execution");
    setExecState([]);
    try {
      const res = await executePhase3Run(projectId, planRunId);
      setExecuteRunId(res.run_id);
      executeRunIdRef.current = res.run_id;
      toast.success("Playwright execution started");
      pollRef.current = setInterval(fetchRunStatus, POLL_MS);
      execPollRef.current = setInterval(fetchExecState, EXEC_POLL_MS);
      fetchRunStatus();
      setExecState(await getPhase3ExecutionState(projectId, res.run_id));
    } catch (err) {
      setPhase("review");
      toast.error(err instanceof ApiError ? err.message : "Execution failed to start");
    }
  }

  // ── Derived state ───────────────────────────────────────────────────────────
  const activeTestCases = testCases.filter(tc => tc.approval_status !== "EXCLUDED");
  const allApproved = activeTestCases.length > 0 && activeTestCases.every(tc => tc.approval_status === "APPROVED");
  const approvedCount = testCases.filter(tc => tc.approval_status === "APPROVED").length;
  const excludedCount = testCases.filter(tc => tc.approval_status === "EXCLUDED").length;
  const isExecuting = phase === "executing" || runStatus?.status === "running";
  const showTestCases = testCases.length > 0 && phase !== "idle";
  const testCasesReadOnly = phase === "executing";
  const normalizedTcSearch = tcSearch.trim().toLowerCase();
  const visibleTestCases = testCases.filter(tc => {
    if (tcFilter !== "ALL" && tc.approval_status !== tcFilter) return false;
    if (!normalizedTcSearch) return true;
    return [
      tc.tc_number,
      tc.title,
      tc.scenario_title,
      tc.target_page,
    ].some(value => String(value ?? "").toLowerCase().includes(normalizedTcSearch));
  });
  const testCaseById = testCases.reduce<Record<string, Phase3TestCase>>((acc, tc) => {
    acc[tc.test_id] = tc;
    return acc;
  }, {});
  const execStateByTestId = execState.reduce<Record<string, Phase3TestState>>((acc, state) => {
    acc[state.test_id] = state;
    return acc;
  }, {});

  // Group TCs by scenario
  const grouped = visibleTestCases.reduce<Record<string, { title: string; tcs: Phase3TestCase[] }>>((acc, tc) => {
    const key = tc.hls_id || "ungrouped";
    if (!acc[key]) acc[key] = { title: tc.scenario_title || "Scenario", tcs: [] };
    acc[key].tcs.push(tc);
    return acc;
  }, {});

  function updateTc(updated: Phase3TestCase) {
    setTestCases(prev => {
      const next = prev.map(t => t.test_id === updated.test_id ? updated : t);
      testCasesRef.current = next;
      return next;
    });
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="space-y-6">

      {/* ── Step 1: Generate ── */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">
            Generate → Approve → Execute
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Status message */}
          <p className="text-sm text-gray-500">
            {allCompleted === null ? "Checking scenario status…"
              : !allCompleted ? "Record at least one HLS scenario before generating tests."
                : phase === "idle" ? "Recorded HLS scenarios are ready. Generate test cases to begin."
                  : phase === "planning" ? "Generating test cases with AI planner…"
                    : phase === "review" ? `${testCases.length} test cases ready for review (${approvedCount}/${activeTestCases.length} active approved, ${excludedCount} excluded).`
                      : phase === "executing" ? "Playwright execution in progress…"
                        : "Run complete."}
          </p>

          {/* Action buttons row */}
          <div className="flex flex-wrap items-center gap-3">

            {/* ⏹ Stop — only visible while planning or executing */}
            {(phase === "planning" || phase === "executing") && (
              <Button
                variant="destructive"
                onClick={handleCancel}
                className="gap-2"
              >
                <Square className="h-4 w-4 fill-current" /> Stop Execution
              </Button>
            )}

            {/* Generate button */}
            <Button
              onClick={handleGenerate}
              disabled={!allCompleted || phase === "planning" || phase === "executing"}
              variant={phase === "idle" || phase === "done" ? "default" : "outline"}
              className="gap-2"
            >
              {phase === "planning" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              {phase === "done" ? "Regenerate" : "Generate Test Cases"}
            </Button>

            {/* Clear Current Run — keeps prior runs intact, drops only the latest. */}
            <Button
              variant="ghost"
              onClick={() => handleReset("current_run")}
              disabled={phase === "planning" || phase === "executing"}
              className="gap-2 text-amber-600 hover:text-amber-700 hover:bg-amber-50"
              title="Delete only the most recent run's test cases, results and review items"
            >
              <Trash2 className="h-4 w-4" /> Clear Current Run
            </Button>

            {/* Clear Everything — hard reset of all Phase 3 data for this project. */}
            <Button
              variant="ghost"
              onClick={() => handleReset("all")}
              disabled={phase === "planning" || phase === "executing"}
              className="gap-2 text-red-500 hover:text-red-700 hover:bg-red-50"
              title="Delete every test case, run and review item for this project"
            >
              <Trash2 className="h-4 w-4" /> Clear Everything
            </Button>

            {/* Approve All — shown in review phase */}
            {phase === "review" && testCases.length > 0 && (
              <Button variant="outline" onClick={handleApproveAll} disabled={approvingAll || allApproved} className="gap-2">
                {approvingAll ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                Approve All
              </Button>
            )}

            {/* Export X-Ray CSV — shown in review phase */}
            {phase === "review" && planRunId && (
              <a href={getPhase3TcDocumentUrl(projectId, planRunId)} download>
                <Button variant="ghost" size="sm" className="gap-2">
                  <Download className="h-4 w-4" /> Export X-Ray CSV
                </Button>
              </a>
            )}

            {/* Execute / Re-run — same endpoint, but relabeled in the 'done' phase
                so the user understands they're firing another run on the same TCs
                rather than starting from scratch. */}
            {(phase === "review" || phase === "executing" || phase === "done") && (
              <Button
                onClick={handleExecute}
                disabled={!allApproved || phase === "executing"}
                variant={phase === "done" ? "outline" : "default"}
                className="gap-2 ml-auto"
                title={
                  !allApproved
                    ? "Approve or exclude all active test cases to enable execution"
                    : phase === "done"
                      ? "Re-run Playwright execution against the same approved test cases"
                      : ""
                }
              >
                {phase === "executing" ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : phase === "done" ? (
                  <RefreshCw className="h-4 w-4" />
                ) : (
                  <Play className="h-4 w-4" />
                )}
                {phase === "executing"
                  ? "Executing…"
                  : phase === "done"
                    ? "Re-run"
                    : "Execute"}
              </Button>
            )}
          </div>

          {/* Approval progress bar */}
          {phase === "review" && testCases.length > 0 && (
            <div className="space-y-1">
              <div className="flex justify-between text-xs text-gray-400">
                <span>Approval progress</span>
                <span>{approvedCount} / {activeTestCases.length} active {excludedCount ? `(${excludedCount} excluded)` : ""}</span>
              </div>
              <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
                <div
                  className="h-full bg-green-500 rounded-full transition-all duration-500"
                  style={{ width: `${activeTestCases.length ? (approvedCount / activeTestCases.length) * 100 : 0}%` }}
                />
              </div>
            </div>
          )}

          {/* Live progress headline + HLS progress bar (shown while a run is active).
              Backed by phase3_progress server-side; populated by the graph as it
              transitions through preflight → A4 → A5 → queuing → running. */}
          {runStatus?.progress && runStatus.status === "running" && (
            <div className="space-y-1.5 rounded-lg border border-blue-100 bg-blue-50/60 p-3">
              <div className="flex items-center gap-2 text-sm text-blue-900">
                <Loader2 className="h-3.5 w-3.5 animate-spin shrink-0" />
                <span className="truncate">{runStatus.progress.headline}</span>
              </div>
              {runStatus.progress.total_hls && runStatus.progress.total_hls > 0 && (
                <div className="h-1 w-full overflow-hidden rounded-full bg-blue-100">
                  <div
                    className="h-full bg-blue-500 transition-all duration-500"
                    style={{
                      width: `${Math.min(
                        100,
                        (((runStatus.progress.current_hls_index ?? 0) + 1) /
                          runStatus.progress.total_hls) *
                          100,
                      )}%`,
                    }}
                  />
                </div>
              )}
            </div>
          )}

          {/* Execution stats */}
          {runStatus && runStatus.run_type === "execute" && (
            <div className="grid grid-cols-4 gap-2 pt-1">
              {[
                { label: "Total", value: runStatus.total, color: "bg-gray-50 border-gray-100" },
                { label: "Passed", value: runStatus.passed, color: "bg-green-50 border-green-100" },
                { label: "Failed", value: runStatus.failed, color: "bg-red-50 border-red-100" },
                { label: "Review", value: runStatus.human_review, color: "bg-amber-50 border-amber-100" },
              ].map(s => (
                <div key={s.label} className={`rounded-lg border p-3 ${s.color}`}>
                  <p className="text-xl font-bold">{s.value}</p>
                  <p className="text-xs text-gray-500">{s.label}</p>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* ── Step 2: TC Approval Accordion ── */}
      {phase !== "idle" && (
        <Phase3TabBar activeTab={activeTab} onChange={setActiveTab} />
      )}

      {activeTab === "testcases" && showTestCases && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              {phase === "planning" || phase === "executing" ? <Loader2 className="h-4 w-4 animate-spin text-blue-500" /> : <CheckCircle2 className="h-4 w-4 text-green-500" />}
              Test Cases
              {/* Plan-run history dropdown (P2.9). Only shown when more than one
                  plan run exists so it doesn't clutter the first-time UX. */}
              {planRuns.length > 1 && (
                <select
                  value={planRunId ?? ""}
                  onChange={(e) => handleSelectPlanRun(e.target.value)}
                  className="ml-2 rounded-md border border-gray-200 bg-white px-2 py-1 text-xs text-gray-700 hover:border-gray-300 focus:border-blue-400 focus:outline-none"
                  title="Switch to a previous plan run"
                >
                  {planRuns.map((r) => {
                    const dt = r.created_at ? new Date(r.created_at) : null;
                    const label = dt
                      ? `${dt.toLocaleDateString()} ${dt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
                      : r.run_id.slice(0, 8);
                    return (
                      <option key={r.run_id} value={r.run_id}>
                        {label} · {r.total} TCs
                      </option>
                    );
                  })}
                </select>
              )}
              {testCasesReadOnly && (
                <span className="rounded-full bg-blue-50 px-2 py-0.5 text-xs font-normal text-blue-700">
                  locked during execution
                </span>
              )}
              <span className="ml-auto text-xs font-normal text-gray-400">
                {visibleTestCases.length} shown / {testCases.length} total
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-col gap-3 rounded-xl border border-gray-100 bg-gray-50/60 p-3 sm:flex-row sm:items-center">
              <Input
                value={tcSearch}
                onChange={e => setTcSearch(e.target.value)}
                placeholder="Search by TC number, title, scenario, or page"
                className="bg-white"
              />
              <select
                value={tcFilter}
                onChange={e => setTcFilter(e.target.value as TestCaseFilter)}
                className="h-10 rounded-md border border-gray-200 bg-white px-3 text-sm text-gray-700 focus:border-blue-400 focus:outline-none"
                title="Filter test cases by approval status"
              >
                <option value="ALL">All statuses</option>
                <option value="PENDING">Pending</option>
                <option value="APPROVED">Approved</option>
                <option value="NEEDS_EDIT">Needs Edit</option>
                <option value="EXCLUDED">Excluded</option>
              </select>
            </div>

            {Object.keys(grouped).length === 0 && (
              <div className="rounded-xl border border-dashed border-gray-200 p-6 text-center text-sm text-gray-500">
                No test cases match the current filter.
              </div>
            )}

            {Object.entries(grouped).map(([key, group]) => (
              <div key={key}>
                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                  {group.title}
                </p>
                <div className="space-y-2">
                  {group.tcs.map(tc => (
                    <TcAccordion
                      key={tc.test_id}
                      tc={tc}
                      projectId={projectId}
                      onUpdate={updateTc}
                      executionState={execStateByTestId[tc.test_id]}
                      readOnly={testCasesReadOnly}
                    />
                  ))}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {/* ── Live Execution Log ── */}
      {activeTab === "execution" && (() => {
        // Tests transition PENDING → terminal status. Workers don't publish a
        // "RUNNING" state, so the first non-terminal test in DB order is the
        // best heuristic for "what's executing right now" in the demo.
        const TERMINAL = new Set(["PASS", "FAIL", "SCRIPT_ERROR", "APP_ERROR", "HUMAN_REVIEW", "BLOCKED"]);
        const completed = execState.filter(t => TERMINAL.has(t.status)).length;
        const currentTest = execState.find(t => !TERMINAL.has(t.status));
        return (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Activity className="h-4 w-4 text-blue-500" />
              Live Execution Log
              {isExecuting && (
                <span className="inline-flex items-center gap-1 rounded-full bg-blue-100 px-2 py-0.5 text-xs text-blue-700">
                  <Loader2 className="h-3 w-3 animate-spin" /> Live
                </span>
              )}
              <span className="ml-auto text-xs font-normal text-gray-400">
                {completed} / {execState.length} done
              </span>
            </CardTitle>
            {isExecuting && currentTest && (
              <p className="mt-1 flex items-center gap-1.5 text-xs text-gray-500">
                <Loader2 className="h-3 w-3 animate-spin text-blue-500 shrink-0" />
                <span className="truncate">
                  Currently running:{" "}
                  <span className="font-medium text-gray-700">{currentTest.title}</span>
                </span>
              </p>
            )}
          </CardHeader>
          <CardContent className="p-0">
            {execState.length === 0 ? (
              <div className="p-6 text-center text-sm text-gray-500">
                Execution has not published test-state rows yet. Start a run to watch Playwright progress here.
              </div>
            ) : (
            <div className="divide-y max-h-96 overflow-y-auto">
              {execState.map(t => {
                const dot = EXEC_STATUS[t.status]?.dot ?? "bg-gray-300";
                const tc = testCaseById[t.test_id];
                return (
                  <div key={t.test_id} className="flex items-start gap-3 px-5 py-3 hover:bg-gray-50">
                    <div className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${dot}`} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        {tc?.tc_number && (
                          <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs font-semibold text-gray-500">
                            {tc.tc_number}
                          </span>
                        )}
                        <span className="truncate text-sm font-medium text-gray-800">{t.title}</span>
                        <StatusBadge status={t.status} />
                        {t.retries > 0 && (
                          <span className="flex items-center gap-1 rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-500">
                            <RefreshCw className="h-3 w-3" /> {t.retries}
                          </span>
                        )}
                        {t.network_logs_count > 0 && (
                          <NetworkLogsBadge
                            projectId={projectId}
                            testId={t.test_id}
                            count={t.network_logs_count}
                            runId={executeRunId ?? undefined}
                          />
                        )}
                        {t.review_category && (
                          <span className="rounded-full bg-amber-50 px-2 py-0.5 text-xs font-semibold text-amber-700">
                            {t.review_category}
                          </span>
                        )}
                        {t.jira_ref && (
                          <span className="rounded-full bg-blue-50 px-2 py-0.5 text-xs font-semibold text-blue-700">
                            {t.jira_ref}
                          </span>
                        )}
                      </div>
                      {t.target_page && <p className="mt-0.5 text-xs text-gray-400">→ {t.target_page}</p>}
                      {t.blocked_by && <p className="mt-0.5 text-xs text-purple-500">Blocked by: {t.blocked_by.slice(0, 8)}…</p>}
                      {t.failure_reason && (
                        <p className="mt-1 line-clamp-2 rounded bg-amber-50 px-2 py-1 text-xs text-amber-800">
                          {t.failure_reason}
                        </p>
                      )}
                      {t.trace_path && (
                        <p className="mt-1 truncate font-mono text-[11px] text-gray-400" title={t.trace_path}>
                          Trace: {t.trace_path}
                        </p>
                      )}
                    </div>
                    <span className="shrink-0 font-mono text-xs text-gray-300">{t.test_id.slice(0, 8)}</span>
                  </div>
                );
              })}
            </div>
            )}
          </CardContent>
        </Card>
        );
      })()}


      {/* ── Review Queue — always rendered; shows empty state when no items ── */}
      {activeTab === "execution" && (
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-base">Review Queue</CardTitle>
            {runStatus?.human_review != null && runStatus.human_review > 0 && (
              <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-700">
                {runStatus.human_review} pending
              </span>
            )}
          </div>
        </CardHeader>
        <CardContent>
          <Phase3ReviewQueue
            projectId={projectId}
            active={phase === "executing" || phase === "done"}
            runId={executeRunId}
            testCases={testCases}
          />
        </CardContent>
      </Card>
      )}

      {activeTab === "report" && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <CheckCircle2 className="h-4 w-4 text-green-500" />
              Final Execution Report
              {planRunId && (
                <a href={getPhase3TcDocumentUrl(projectId, planRunId)} download className="ml-auto">
                  <Button variant="ghost" size="sm" className="gap-2">
                    <Download className="h-4 w-4" /> Export X-Ray CSV
                  </Button>
                </a>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {runStatus?.run_type === "execute" ? (
              <>
                <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
                  {[
                    { label: "Total", value: runStatus.total, color: "bg-gray-50 border-gray-100" },
                    { label: "Passed", value: runStatus.passed, color: "bg-green-50 border-green-100" },
                    { label: "Failed", value: runStatus.failed, color: "bg-red-50 border-red-100" },
                    { label: "Review", value: runStatus.human_review, color: "bg-amber-50 border-amber-100" },
                    { label: "Skipped", value: runStatus.skipped, color: "bg-purple-50 border-purple-100" },
                  ].map(s => (
                    <div key={s.label} className={`rounded-lg border p-3 ${s.color}`}>
                      <p className="text-xl font-bold">{s.value}</p>
                      <p className="text-xs text-gray-500">{s.label}</p>
                    </div>
                  ))}
                </div>

                {execState.length === 0 ? (
                  <div className="rounded-xl border border-dashed border-gray-200 p-6 text-center text-sm text-gray-500">
                    No per-test execution rows are available yet.
                  </div>
                ) : (
                  <div className="overflow-hidden rounded-xl border border-gray-100">
                    <div className="grid grid-cols-[110px_1fr_130px_1.4fr_130px] gap-3 bg-gray-50 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
                      <span>TC</span>
                      <span>Test</span>
                      <span>Status</span>
                      <span>Reason</span>
                      <span>Evidence</span>
                    </div>
                    <div className="divide-y divide-gray-100">
                      {execState.map(row => {
                        const tc = testCaseById[row.test_id];
                        return (
                          <div key={row.test_id} className="grid grid-cols-[110px_1fr_130px_1.4fr_130px] gap-3 px-4 py-3 text-sm">
                            <span className="font-mono text-xs text-gray-500">
                              {tc?.tc_number ?? row.tc_number ?? row.test_id.slice(0, 8)}
                            </span>
                            <span className="min-w-0 truncate font-medium text-gray-800">
                              {tc?.title ?? row.title}
                            </span>
                            <StatusBadge status={row.status} />
                            <span className="min-w-0 text-xs text-gray-600">
                              {row.failure_reason ? (
                                <span className="line-clamp-2" title={row.failure_reason}>
                                  {row.review_category ? `${row.review_category}: ` : ""}{row.failure_reason}
                                </span>
                              ) : row.jira_ref ? (
                                <span className="font-semibold text-blue-700">{row.jira_ref}</span>
                              ) : (
                                <span className="text-gray-400">-</span>
                              )}
                            </span>
                            <span className="text-xs text-gray-500">
                              <span className="block">
                                {row.network_logs_count > 0
                                  ? `${row.network_logs_count} network logs`
                                  : row.trace_path
                                    ? "Trace available"
                                    : "No network errors"}
                              </span>
                              {(row.review_status || row.jira_ref) && (
                                <span className="block truncate text-[11px]">
                                  {row.review_status ? `Review: ${row.review_status}` : ""}
                                  {row.review_status && row.jira_ref ? " · " : ""}
                                  {row.jira_ref ?? ""}
                                </span>
                              )}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div className="rounded-xl border border-dashed border-gray-200 p-6 text-center text-sm text-gray-500">
                Execute approved test cases to generate the final pass/fail report.
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
