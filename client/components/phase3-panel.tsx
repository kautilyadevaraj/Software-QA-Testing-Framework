"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity, CheckCircle2, ChevronDown, ChevronRight,
  Download, Loader2, Pencil, Play, RefreshCw, Save, Square, Trash2, ThumbsDown, ThumbsUp, X, XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  ApiError,
  approveAllPhase3TestCases,
  cancelPhase3Run,
  executePhase3Run,
  getPhase3ExecutionState,
  getPhase3RunStatus,
  getPhase3TcDocumentJson,
  getPhase3TcDocumentUrl,
  listHighLevelScenarios,
  planPhase3Run,
  resetPhase3,
  setPhase3TestCaseApproval,
  updatePhase3TestCase,
  type Phase3RunStatus,
  type Phase3TestCase,
  type Phase3TestState,
} from "@/lib/api";
import { Phase3ReviewQueue } from "@/components/phase3-review-queue";

// ── Types ─────────────────────────────────────────────────────────────────────

type UiPhase = "idle" | "planning" | "review" | "executing" | "done";

type Props = { projectId: string };

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

function ApprovalChip({ tc, projectId, onUpdate }: {
  tc: Phase3TestCase; projectId: string; onUpdate: (updated: Phase3TestCase) => void;
}) {
  const [loading, setLoading] = useState(false);

  async function patch(s: "APPROVED" | "NEEDS_EDIT") {
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
      <button onClick={() => patch("NEEDS_EDIT")}
        className="inline-flex items-center gap-1 rounded-full bg-green-100 px-2.5 py-1 text-xs font-semibold text-green-700 hover:bg-green-200 transition-colors">
        <CheckCircle2 className="h-3 w-3" /> Approved
      </button>
    );
  }
  if (tc.approval_status === "NEEDS_EDIT") {
    return (
      <button onClick={() => patch("APPROVED")}
        className="inline-flex items-center gap-1 rounded-full bg-red-100 px-2.5 py-1 text-xs font-semibold text-red-700 hover:bg-red-200 transition-colors">
        <XCircle className="h-3 w-3" /> Needs Edit
      </button>
    );
  }
  return (
    <div className="flex items-center gap-1">
      <button onClick={() => patch("APPROVED")}
        className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2.5 py-1 text-xs font-semibold text-gray-600 hover:bg-green-100 hover:text-green-700 transition-colors">
        <ThumbsUp className="h-3 w-3" /> Approve
      </button>
      <button onClick={() => patch("NEEDS_EDIT")}
        className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2.5 py-1 text-xs font-semibold text-gray-600 hover:bg-red-100 hover:text-red-700 transition-colors">
        <ThumbsDown className="h-3 w-3" /> Needs Edit
      </button>
    </div>
  );
}

function TcAccordion({ tc, projectId, onUpdate }: {
  tc: Phase3TestCase; projectId: string; onUpdate: (u: Phase3TestCase) => void;
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);

  // Edit-mode draft state
  const [draftTitle, setDraftTitle] = useState("");
  const [draftSteps, setDraftSteps] = useState("");
  const [draftAC, setDraftAC] = useState("");

  function startEdit() {
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
          <span className="text-xs text-gray-400 shrink-0">{tc.target_page}</span>
        </button>
        {/* ApprovalChip — outside the button, no nested-button issue */}
        <div className="px-3 shrink-0">
          <ApprovalChip tc={tc} projectId={projectId} onUpdate={onUpdate} />
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
                  className="inline-flex items-center gap-1 text-xs text-gray-400 hover:text-blue-600 transition-colors"
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
  const [phase, setPhase] = useState<UiPhase>("idle");
  const [allCompleted, setAllCompleted] = useState<boolean | null>(null);
  const [planRunId, setPlanRunId] = useState<string | null>(null);
  const [testCases, setTestCases] = useState<Phase3TestCase[]>([]);
  const [runStatus, setRunStatus] = useState<Phase3RunStatus | null>(null);
  const [execState, setExecState] = useState<Phase3TestState[]>([]);
  const [approvingAll, setApprovingAll] = useState(false);

  // Ref mirror of testCases so callbacks can read length without stale closure
  const testCasesRef = useRef<Phase3TestCase[]>([]);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const execPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const tcPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopAll = () => {
    [pollRef, execPollRef, tcPollRef].forEach(r => { if (r.current) { clearInterval(r.current); r.current = null; } });
  };

  // Check Phase 2 completion
  const checkScenarios = useCallback(async () => {
    try {
      const d = await listHighLevelScenarios(projectId);
      setAllCompleted(d.scenarios.length > 0 && d.scenarios.every(s => s.status === "completed"));
    } catch { setAllCompleted(false); }
  }, [projectId]);

  // Poll run status — drives phase transitions for both on-mount detection and live polling
  const fetchRunStatus = useCallback(async () => {
    try {
      const s = await getPhase3RunStatus(projectId);
      setRunStatus(s);

      if (s.run_type === "execute") {
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
        setPhase(prev => (prev === "review" ? prev : "review"));
      }
    } catch { /* 404 = no run yet */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // Poll TC document during planning — stop when backend sets status to 'planned'
  const fetchTcDoc = useCallback(async (runId: string) => {
    try {
      const rows = await getPhase3TcDocumentJson(projectId, runId);
      if (rows.length > 0) {
        setTestCases(rows);
        testCasesRef.current = rows;
      }
    } catch { /* not ready yet */ }
  }, [projectId]);

  // Poll exec state
  const fetchExecState = useCallback(async () => {
    try { setExecState(await getPhase3ExecutionState(projectId)); }
    catch { /* ignore */ }
  }, [projectId]);

  useEffect(() => {
    checkScenarios();
    fetchRunStatus(); // on mount: auto-detects running/completed runs and sets phase
    return stopAll;
  }, [checkScenarios, fetchRunStatus]);

  // Secondary effect for planning-complete detection via TC poll
  useEffect(() => {
    if (!runStatus) return;
    if (runStatus.run_type === "plan" && runStatus.status === "planned") {
      if (tcPollRef.current) { clearInterval(tcPollRef.current); tcPollRef.current = null; }
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
    setTestCases([]);
    try {
      const res = await planPhase3Run(projectId);
      setPlanRunId(res.run_id);
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

  // ── Reset: clear all Phase 3 data ────────────────────────────────────────────
  async function handleReset() {
    if (!window.confirm(
      "This will delete ALL test cases, results, and runs for this project. Continue?"
    )) return;
    try {
      stopAll();
      const res = await resetPhase3(projectId);
      setPhase("idle");
      setTestCases([]);
      setRunStatus(null);
      setExecState([]);
      setPlanRunId(null);
      toast.success(
        `Cleared: ${res.deleted_test_cases} TCs, ${res.deleted_runs} runs, ${res.deleted_review_items} review items`
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
      setTestCases(prev => prev.map(tc => ({ ...tc, approval_status: "APPROVED" as const })));
      toast.success(`Approved ${res.approved_count} test cases`);
    } catch { toast.error("Failed to approve all"); }
    finally { setApprovingAll(false); }
  }

  // ── Step 3: Execute ─────────────────────────────────────────────────────────
  async function handleExecute() {
    if (!planRunId) return;
    const allApproved = testCases.every(tc => tc.approval_status === "APPROVED");
    if (!allApproved) { toast.error("Approve all test cases before executing"); return; }
    setPhase("executing");
    setExecState([]);
    try {
      await executePhase3Run(projectId, planRunId);
      toast.success("Playwright execution started");
      pollRef.current = setInterval(fetchRunStatus, POLL_MS);
      execPollRef.current = setInterval(fetchExecState, EXEC_POLL_MS);
      fetchRunStatus();
      fetchExecState();
    } catch (err) {
      setPhase("review");
      toast.error(err instanceof ApiError ? err.message : "Execution failed to start");
    }
  }

  // ── Derived state ───────────────────────────────────────────────────────────
  const allApproved = testCases.length > 0 && testCases.every(tc => tc.approval_status === "APPROVED");
  const approvedCount = testCases.filter(tc => tc.approval_status === "APPROVED").length;
  const isExecuting = phase === "executing" || runStatus?.status === "running";

  // Group TCs by scenario
  const grouped = testCases.reduce<Record<string, { title: string; tcs: Phase3TestCase[] }>>((acc, tc) => {
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
              : !allCompleted ? "Complete all Phase 2 scenarios before generating tests."
                : phase === "idle" ? "All scenarios ready. Generate test cases to begin."
                  : phase === "planning" ? "Generating test cases with AI planner…"
                    : phase === "review" ? `${testCases.length} test cases ready for review (${approvedCount}/${testCases.length} approved).`
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

            {/* Clear All — always available, resets DB + in-memory state */}
            <Button
              variant="ghost"
              onClick={handleReset}
              disabled={phase === "planning" || phase === "executing"}
              className="gap-2 text-red-500 hover:text-red-700 hover:bg-red-50"
              title="Delete all test cases, runs and review items for this project"
            >
              <Trash2 className="h-4 w-4" /> Clear All
            </Button>

            {/* Approve All — shown in review phase */}
            {phase === "review" && testCases.length > 0 && (
              <Button variant="outline" onClick={handleApproveAll} disabled={approvingAll || allApproved} className="gap-2">
                {approvingAll ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                Approve All
              </Button>
            )}

            {/* Export MD — shown in review phase */}
            {phase === "review" && planRunId && (
              <a href={getPhase3TcDocumentUrl(projectId, planRunId)} download>
                <Button variant="ghost" size="sm" className="gap-2">
                  <Download className="h-4 w-4" /> Export MD
                </Button>
              </a>
            )}

            {/* Execute button */}
            {(phase === "review" || phase === "executing" || phase === "done") && (
              <Button
                onClick={handleExecute}
                disabled={!allApproved || phase === "executing"}
                className="gap-2 ml-auto"
                title={!allApproved ? "Approve all test cases to enable execution" : ""}
              >
                {phase === "executing" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                {phase === "executing" ? "Executing…" : "Execute"}
              </Button>
            )}
          </div>

          {/* Approval progress bar */}
          {phase === "review" && testCases.length > 0 && (
            <div className="space-y-1">
              <div className="flex justify-between text-xs text-gray-400">
                <span>Approval progress</span>
                <span>{approvedCount} / {testCases.length}</span>
              </div>
              <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
                <div
                  className="h-full bg-green-500 rounded-full transition-all duration-500"
                  style={{ width: `${testCases.length ? (approvedCount / testCases.length) * 100 : 0}%` }}
                />
              </div>
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
      {testCases.length > 0 && (phase === "review" || phase === "planning") && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              {phase === "planning" ? <Loader2 className="h-4 w-4 animate-spin text-blue-500" /> : <CheckCircle2 className="h-4 w-4 text-green-500" />}
              Test Cases
              <span className="ml-auto text-xs font-normal text-gray-400">{testCases.length} total</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {Object.entries(grouped).map(([key, group]) => (
              <div key={key}>
                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                  {group.title}
                </p>
                <div className="space-y-2">
                  {group.tcs.map(tc => (
                    <TcAccordion key={tc.test_id} tc={tc} projectId={projectId} onUpdate={updateTc} />
                  ))}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {/* ── Live Execution Log ── */}
      {execState.length > 0 && (
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
              <span className="ml-auto text-xs font-normal text-gray-400">{execState.length} tests</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <div className="divide-y max-h-96 overflow-y-auto">
              {execState.map(t => {
                const dot = EXEC_STATUS[t.status]?.dot ?? "bg-gray-300";
                return (
                  <div key={t.test_id} className="flex items-start gap-3 px-5 py-3 hover:bg-gray-50">
                    <div className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${dot}`} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate text-sm font-medium text-gray-800">{t.title}</span>
                        <StatusBadge status={t.status} />
                        {t.retries > 0 && (
                          <span className="flex items-center gap-1 rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-500">
                            <RefreshCw className="h-3 w-3" /> {t.retries}
                          </span>
                        )}
                        {t.network_logs_count > 0 && (
                          <span className="rounded-full bg-red-50 px-2 py-0.5 text-xs text-red-600">
                            {t.network_logs_count} 4xx/5xx
                          </span>
                        )}
                      </div>
                      {t.target_page && <p className="mt-0.5 text-xs text-gray-400">→ {t.target_page}</p>}
                      {t.blocked_by && <p className="mt-0.5 text-xs text-purple-500">Blocked by: {t.blocked_by.slice(0, 8)}…</p>}
                    </div>
                    <span className="shrink-0 font-mono text-xs text-gray-300">{t.test_id.slice(0, 8)}</span>
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      )}


      {/* ── Review Queue — always rendered; shows empty state when no items ── */}
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
          />
        </CardContent>
      </Card>
    </div>
  );
}
