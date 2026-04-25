"use client";

import { useCallback, useEffect, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  ClipboardCopy,
  Loader2,
  Lock,
  Plus,
  Trash2,
  Pencil,
  Terminal,
  Circle,
  PlayCircle,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";
import {
  createScenario,
  deleteScenario,
  getPhase2Status,
  getRecordingSetup,
  listRecordingSessions,
  listScenarios,
  lockScenarios,
  updateScenario,
  ApiError,
  type Phase2StatusResponse,
  type RecordingSessionResponse,
  type RecordingSetupResponse,
  type ScenarioResponse,
} from "@/lib/api";

interface Phase2TestingProps {
  projectId: string;
}

// ── Helpers ────────────────────────────────────────────────────────────────

function RecordingStatusBadge({ status }: { status: string | null }) {
  if (!status || status === "pending") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-500">
        <Circle className="h-2.5 w-2.5" /> Pending
      </span>
    );
  }
  if (status === "in_progress") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2 py-0.5 text-xs text-[#2a63f5]">
        <Loader2 className="h-2.5 w-2.5 animate-spin" /> Recording…
      </span>
    );
  }
  if (status === "completed") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-xs text-emerald-700">
        <CheckCircle2 className="h-2.5 w-2.5" /> Recorded
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-red-50 px-2 py-0.5 text-xs text-red-600">
      Failed
    </span>
  );
}

function SourceBadge({ source }: { source: string }) {
  const label =
    source === "agent_1"
      ? "Agent 1"
      : source === "agent_2"
      ? "Agent 2"
      : "Manual";
  return (
    <span className="inline-flex rounded-full bg-[#2a63f5]/10 px-2 py-0.5 text-[10px] font-semibold text-[#2a63f5]">
      {label}
    </span>
  );
}

// ── Sub-component: Scenario Review Step ───────────────────────────────────

interface ScenarioReviewProps {
  projectId: string;
  onLocked: () => void;
}

function ScenarioReview({ projectId, onLocked }: ScenarioReviewProps) {
  const [scenarios, setScenarios] = useState<ScenarioResponse[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isLocking, setIsLocking] = useState(false);

  // New scenario form
  const [showAddForm, setShowAddForm] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [isAdding, setIsAdding] = useState(false);

  // Edit state: scenarioId → draft values
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [isSavingEdit, setIsSavingEdit] = useState(false);

  const load = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await listScenarios(projectId);
      setScenarios(data.items);
    } catch {
      toast.error("Failed to load scenarios");
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleAdd = async () => {
    if (!newTitle.trim()) {
      toast.error("Title is required");
      return;
    }
    setIsAdding(true);
    try {
      const created = await createScenario(projectId, {
        title: newTitle.trim(),
        description: newDescription.trim() || undefined,
        source: "manual",
      });
      setScenarios((prev) => [...prev, created]);
      setNewTitle("");
      setNewDescription("");
      setShowAddForm(false);
      toast.success("Scenario added");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Failed to add scenario");
    } finally {
      setIsAdding(false);
    }
  };

  const startEdit = (s: ScenarioResponse) => {
    setEditingId(s.id);
    setEditTitle(s.title);
    setEditDescription(s.description ?? "");
  };

  const handleSaveEdit = async (scenarioId: string) => {
    if (!editTitle.trim()) {
      toast.error("Title is required");
      return;
    }
    setIsSavingEdit(true);
    try {
      const updated = await updateScenario(projectId, scenarioId, {
        title: editTitle.trim(),
        description: editDescription.trim() || undefined,
      });
      setScenarios((prev) =>
        prev.map((s) => (s.id === scenarioId ? updated : s))
      );
      setEditingId(null);
      toast.success("Scenario updated");
    } catch {
      toast.error("Failed to update scenario");
    } finally {
      setIsSavingEdit(false);
    }
  };

  const handleDelete = async (scenarioId: string) => {
    try {
      await deleteScenario(projectId, scenarioId);
      setScenarios((prev) => prev.filter((s) => s.id !== scenarioId));
      toast.success("Scenario deleted");
    } catch {
      toast.error("Failed to delete scenario");
    }
  };

  const handleLock = async () => {
    if (scenarios.length === 0) {
      toast.error("Add at least one scenario before locking");
      return;
    }
    setIsLocking(true);
    try {
      await lockScenarios(projectId);
      toast.success("Scenarios locked — recording sessions created");
      onLocked();
    } catch (e) {
      toast.error(
        e instanceof ApiError ? e.message : "Failed to lock scenarios"
      );
    } finally {
      setIsLocking(false);
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-[#2a63f5]" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-black">Scenario List</h3>
          <p className="text-xs text-black/60">
            Review, edit, or add scenarios. Lock the list when ready to begin
            recording.
          </p>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setShowAddForm((v) => !v)}
          className="gap-1.5"
        >
          <Plus className="h-3.5 w-3.5" />
          Add Scenario
        </Button>
      </div>

      {/* Add form */}
      {showAddForm && (
        <div className="rounded-lg border border-dashed border-[#2a63f5]/40 bg-[#2a63f5]/5 p-4 space-y-3">
          <Label className="text-xs font-semibold uppercase tracking-wide text-black/60">
            New Scenario
          </Label>
          <Input
            placeholder="Scenario title *"
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
          />
          <textarea
            rows={2}
            placeholder="Description (optional)"
            value={newDescription}
            onChange={(e) => setNewDescription(e.target.value)}
            className="flex w-full resize-none rounded-md border border-black/20 bg-white px-3 py-2 text-sm text-black placeholder:text-black/45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2a63f5]"
          />
          <div className="flex gap-2">
            <Button size="sm" onClick={handleAdd} disabled={isAdding}>
              {isAdding ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                "Add"
              )}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setShowAddForm(false);
                setNewTitle("");
                setNewDescription("");
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      {/* Scenario list */}
      {scenarios.length === 0 ? (
        <div className="rounded-lg border border-dashed border-black/15 bg-gray-50 px-4 py-8 text-center">
          <p className="text-sm text-black/50">
            No scenarios yet. Add one manually or wait for AI generation to
            complete.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {scenarios.map((scenario) => (
            <div
              key={scenario.id}
              className="rounded-lg border border-black/10 bg-white p-4"
            >
              {editingId === scenario.id ? (
                /* Edit mode */
                <div className="space-y-2">
                  <Input
                    value={editTitle}
                    onChange={(e) => setEditTitle(e.target.value)}
                    placeholder="Title"
                  />
                  <textarea
                    rows={2}
                    value={editDescription}
                    onChange={(e) => setEditDescription(e.target.value)}
                    placeholder="Description"
                    className="flex w-full resize-none rounded-md border border-black/20 bg-white px-3 py-2 text-sm text-black placeholder:text-black/45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2a63f5]"
                  />
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      onClick={() => handleSaveEdit(scenario.id)}
                      disabled={isSavingEdit}
                    >
                      {isSavingEdit ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        "Save"
                      )}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setEditingId(null)}
                    >
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : (
                /* View mode */
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-sm font-semibold text-black">
                        {scenario.title}
                      </p>
                      <SourceBadge source={scenario.source} />
                    </div>
                    {scenario.description && (
                      <p className="mt-1 text-xs text-black/60 leading-relaxed">
                        {scenario.description}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <button
                      onClick={() => startEdit(scenario)}
                      className="rounded p-1.5 text-black/40 hover:bg-black/5 hover:text-black"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                    <button
                      onClick={() => handleDelete(scenario.id)}
                      className="rounded p-1.5 text-red-400 hover:bg-red-50 hover:text-red-600"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Lock button */}
      <div className="flex justify-end pt-2">
        <Button
          onClick={handleLock}
          disabled={isLocking || scenarios.length === 0}
          className="bg-[#2a63f5] hover:bg-[#2a63f5]/90 gap-2"
        >
          {isLocking ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Lock className="h-4 w-4" />
          )}
          Lock &amp; Proceed to Recording
        </Button>
      </div>
    </div>
  );
}

// ── Sub-component: Recording Setup Step ───────────────────────────────────

interface RecordingSetupProps {
  projectId: string;
  allRecorded: boolean;
  onAllRecorded: () => void;
}

function RecordingSetup({
  projectId,
  allRecorded,
  onAllRecorded,
}: RecordingSetupProps) {
  const [setup, setSetup] = useState<RecordingSetupResponse | null>(null);
  const [sessions, setSessions] = useState<RecordingSessionResponse[]>([]);
  const [copied, setCopied] = useState(false);
  const [isLoadingSetup, setIsLoadingSetup] = useState(true);

  const loadSetup = useCallback(async () => {
    try {
      const [setupData, sessionsData] = await Promise.all([
        getRecordingSetup(projectId),
        listRecordingSessions(projectId),
      ]);
      setSetup(setupData);
      setSessions(sessionsData.items);
    } catch {
      toast.error("Failed to load recording setup");
    } finally {
      setIsLoadingSetup(false);
    }
  }, [projectId]);

  useEffect(() => {
    void loadSetup();
  }, [loadSetup]);

  // Poll recording sessions every 5 seconds until all recorded
  useEffect(() => {
    if (allRecorded) return;

    const interval = setInterval(async () => {
      try {
        const data = await listRecordingSessions(projectId);
        setSessions(data.items);
        const allDone =
          data.items.length > 0 &&
          data.items.every((s) => s.status === "completed");
        if (allDone) {
          onAllRecorded();
          clearInterval(interval);
        }
      } catch {
        // silently ignore polling errors
      }
    }, 5000);

    return () => clearInterval(interval);
  }, [projectId, allRecorded, onAllRecorded]);

  const handleCopy = async () => {
    if (!setup) return;
    await navigator.clipboard.writeText(setup.setup_command);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const completedCount = sessions.filter(
    (s) => s.status === "completed"
  ).length;
  const totalCount = sessions.length;

  if (isLoadingSetup) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-[#2a63f5]" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Progress summary */}
      <div className="rounded-lg border border-black/10 bg-white p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-black">
            Recording Progress
          </h3>
          <span className="text-xs text-black/60">
            {completedCount} / {totalCount} recorded
          </span>
        </div>
        <div className="h-2 w-full overflow-hidden rounded-full bg-gray-100">
          <div
            className="h-full bg-[#2a63f5] transition-all duration-500"
            style={{
              width:
                totalCount > 0
                  ? `${(completedCount / totalCount) * 100}%`
                  : "0%",
            }}
          />
        </div>
      </div>

      {/* Setup command */}
      {setup && (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Terminal className="h-4 w-4 text-black/60" />
            <h3 className="text-sm font-semibold text-black">
              Recorder Setup Command
            </h3>
          </div>
          <p className="text-xs text-black/60">
            Run this command on your local machine to download and launch the
            recorder. The browser will open — navigate through the application
            for each scenario.
          </p>
          <div className="relative rounded-lg border border-black/15 bg-gray-950 p-4">
            <code className="block break-all text-xs text-emerald-400 leading-relaxed pr-10">
              {setup.setup_command}
            </code>
            <button
              onClick={handleCopy}
              className="absolute right-3 top-3 rounded p-1.5 text-gray-400 hover:bg-white/10 hover:text-white transition-colors"
            >
              <ClipboardCopy className="h-4 w-4" />
            </button>
          </div>
          {copied && (
            <p className="text-xs text-emerald-600 font-medium">
              ✓ Copied to clipboard
            </p>
          )}
        </div>
      )}

      {/* Per-scenario recording status */}
      <div className="space-y-2">
        <h3 className="text-sm font-semibold text-black">
          Scenario Recording Status
        </h3>
        {sessions.length === 0 ? (
          <p className="text-xs text-black/50">No sessions yet.</p>
        ) : (
          <div className="space-y-2">
            {sessions.map((session) => (
              <div
                key={session.id}
                className={cn(
                  "flex items-center justify-between rounded-lg border px-4 py-3",
                  session.status === "completed"
                    ? "border-emerald-200 bg-emerald-50"
                    : session.status === "in_progress"
                    ? "border-[#2a63f5]/30 bg-[#2a63f5]/5"
                    : session.status === "failed"
                    ? "border-red-200 bg-red-50"
                    : "border-black/10 bg-white"
                )}
              >
                <div className="flex items-center gap-3 min-w-0">
                  {session.status === "completed" ? (
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
                  ) : session.status === "in_progress" ? (
                    <Loader2 className="h-4 w-4 shrink-0 animate-spin text-[#2a63f5]" />
                  ) : (
                    <Circle className="h-4 w-4 shrink-0 text-gray-300" />
                  )}
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-black truncate">
                      {session.scenario_title}
                    </p>
                    {session.status === "completed" && (
                      <p className="text-xs text-black/50">
                        {session.step_count} steps captured
                      </p>
                    )}
                  </div>
                </div>
                <RecordingStatusBadge status={session.status} />
              </div>
            ))}
          </div>
        )}
      </div>

      {/* All recorded banner */}
      {allRecorded && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-center">
          <CheckCircle2 className="mx-auto h-8 w-8 text-emerald-600 mb-2" />
          <p className="text-sm font-semibold text-emerald-800">
            All scenarios recorded!
          </p>
          <p className="text-xs text-emerald-700 mt-1">
            You can now proceed to test script generation.
          </p>
        </div>
      )}
    </div>
  );
}

// ── Main Phase2Testing Component ──────────────────────────────────────────

export function Phase2Testing({ projectId }: Phase2TestingProps) {
  const [status, setStatus] = useState<Phase2StatusResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const loadStatus = useCallback(async () => {
    try {
      const data = await getPhase2Status(projectId);
      setStatus(data);
    } catch {
      toast.error("Failed to load Phase 2 status");
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  // Determine which sub-step to show
  const subStep: "review" | "recording" | "done" = !status?.phase_2_locked
    ? "review"
    : status.all_recorded
    ? "done"
    : "recording";

  if (isLoading || !status) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-6 w-6 animate-spin text-[#2a63f5]" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Phase progress indicator */}
      <div className="flex items-center gap-0">
        {(
          [
            { key: "review", label: "1. Review Scenarios" },
            { key: "recording", label: "2. UI Discovery" },
            { key: "done", label: "3. Generate Scripts" },
          ] as const
        ).map((step, idx) => {
          const isActive = step.key === subStep;
          const isPast =
            (subStep === "recording" && step.key === "review") ||
            (subStep === "done" &&
              (step.key === "review" || step.key === "recording"));

          return (
            <div key={step.key} className="flex items-center">
              {idx > 0 && (
                <div
                  className={cn(
                    "h-px w-8 shrink-0",
                    isPast ? "bg-[#2a63f5]" : "bg-black/15"
                  )}
                />
              )}
              <div
                className={cn(
                  "flex items-center gap-2 rounded-full px-3 py-1.5 text-xs font-medium",
                  isActive
                    ? "bg-[#2a63f5] text-white"
                    : isPast
                    ? "bg-[#2a63f5]/15 text-[#2a63f5]"
                    : "bg-gray-100 text-black/40"
                )}
              >
                {isPast && <CheckCircle2 className="h-3 w-3" />}
                {step.label}
              </div>
            </div>
          );
        })}
      </div>

      {/* Sub-step content */}
      <div className="rounded-lg border border-black/10 bg-white p-6">
        {subStep === "review" && (
          <>
            <div className="mb-4">
              <h2 className="text-base font-semibold text-black">
                Step 1 — Review &amp; Approve Scenarios
              </h2>
              <p className="text-sm text-black/60 mt-1">
                These scenarios were generated from your project documents.
                Review, edit, or add any that are missing, then lock the list to
                begin UI recording.
              </p>
            </div>
            <ScenarioReview
              projectId={projectId}
              onLocked={() =>
                setStatus((prev) =>
                  prev ? { ...prev, phase_2_locked: true } : prev
                )
              }
            />
          </>
        )}

        {subStep === "recording" && (
          <>
            <div className="mb-4">
              <h2 className="text-base font-semibold text-black">
                Step 2 — UI Discovery Recording
              </h2>
              <p className="text-sm text-black/60 mt-1">
                Run the recorder on your local machine and navigate through each
                scenario in the browser. The system will capture page structure,
                element selectors, and action sequences in real time.
              </p>
            </div>
            <RecordingSetup
              projectId={projectId}
              allRecorded={status.all_recorded}
              onAllRecorded={() =>
                setStatus((prev) =>
                  prev ? { ...prev, all_recorded: true } : prev
                )
              }
            />
          </>
        )}

        {subStep === "done" && (
          <div className="flex flex-col items-center py-8 text-center">
            <CheckCircle2 className="h-12 w-12 text-emerald-500 mb-4" />
            <h2 className="text-lg font-semibold text-black mb-2">
              UI Discovery Complete
            </h2>
            <p className="text-sm text-black/60 max-w-sm">
              All {status.total_scenarios} scenarios have been recorded. The
              route registry and step sequences are ready for test script
              generation.
            </p>
            <Button
              className="mt-6 bg-[#2a63f5] hover:bg-[#2a63f5]/90 gap-2"
              disabled
            >
              <PlayCircle className="h-4 w-4" />
              Proceed to Script Generation
              <span className="ml-1 rounded-full bg-white/20 px-2 py-0.5 text-[10px]">
                Phase 3
              </span>
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
