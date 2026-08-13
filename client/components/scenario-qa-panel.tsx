"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { BrushCleaning, Check, ChevronDown, ChevronUp, Info, Loader2, Pencil, Play, Plus, Save, Trash2, WandSparkles, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  ApiError,
  approveHighLevelScenarios,
  clearScenarioRecording,
  createHighLevelScenario,
  deleteHighLevelScenario,
  fetchTestDocumentRows,
  generateHighLevelScenariosStream,
  getRecordingSetup,
  getScenarioRecordingStatus,
  listHighLevelScenarios,
  triggerScenarioLaunch,
  updateHighLevelScenario,
  type ScenarioAccessMode,
  type ScenarioLevel,
  type ScenarioGenerationType,
  type HighLevelScenario,
  type PreviewScenario,
  type RecordingSetupResponse,
  type ScenarioSource,
  type TestDocumentRow,
  type TestDocumentSheet,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type ScenarioQaPanelProps = {
  projectId: string;
  currentUserId: string | null;
};

type DraftScenario = {
  title: string;
  description: string;
  test_id?: string;
  pre_conditions?: string;
  test_steps?: string;
  expected_result?: string;
  sheet_name?: string | null;
};

function mapTestDocumentRow(row: TestDocumentRow, sheetName?: string): PreviewScenario {
  const parts: string[] = [];
  if (row.pre_conditions) parts.push(`Pre-Conditions:\n${row.pre_conditions}`);
  if (row.test_steps) parts.push(`Test Steps:\n${row.test_steps}`);
  if (row.expected_result) parts.push(`Expected Result:\n${row.expected_result}`);
  return {
    title: row.test_scenario || row.test_id || "Untitled scenario",
    description: parts.join("\n\n"),
    source: "manual",
    test_id: row.test_id,
    pre_conditions: row.pre_conditions,
    test_steps: row.test_steps,
    expected_result: row.expected_result,
    sheet_name: sheetName ?? null,
  };
}

function buildDescriptionFromDraft(draft: DraftScenario) {
  const parts: string[] = [];
  if (draft.pre_conditions?.trim()) parts.push(`Pre-Conditions:\n${draft.pre_conditions.trim()}`);
  if (draft.test_steps?.trim()) parts.push(`Test Steps:\n${draft.test_steps.trim()}`);
  if (draft.expected_result?.trim()) parts.push(`Expected Result:\n${draft.expected_result.trim()}`);
  return parts.join("\n\n");
}



const SCENARIO_LIMIT_OPTIONS = [
  { label: "1 - 20", value: "20" },
  { label: "1 - 50", value: "50" },
  { label: "1 - 100", value: "100" },
  { label: "Max", value: "max" },
  { label: "Custom", value: "custom" },
] as const;

const SCENARIO_TYPE_OPTIONS: ScenarioGenerationType[] = [
  "ALL",
  "HLS",
  "Functional",
  "Technical",
  "API",
  "Security",
  "Performance",
  "Integration",
  "Data",
  "Compliance",
  "Usability",
];

const ACCESS_MODE_OPTIONS: { label: string; value: ScenarioAccessMode }[] = [
  { label: "UI-only web app", value: "UI_ONLY_WEB" },
  { label: "UI + API docs", value: "UI_AND_API" },
  { label: "Technical observable", value: "TECHNICAL_REVIEW" },
];

const SCENARIO_LEVEL_OPTIONS: { label: string; value: ScenarioLevel }[] = [
  { label: "HLS", value: "HLS" },
  { label: "Detailed HLS", value: "DETAILED_HLS" },
];

const OPTION_GUIDE: {
  title: string;
  description: string;
  options?: { label: string; description: string }[];
}[] = [
  {
    title: "Scenario Range",
    description: "How many new scenarios to generate in this run.",
    options: [
      { label: "1 - 20", description: "Compact batch for quick review." },
      { label: "1 - 50", description: "Medium batch for wider coverage." },
      { label: "1 - 100", description: "Large batch for many workflows." },
      { label: "Max", description: "Use the backend maximum." },
      { label: "Custom", description: "Enter any count from 1 to 500." },
    ],
  },
  {
    title: "Tester Access",
    description: "What the tester can observe while testing.",
    options: [
      { label: "UI-only web app", description: "Only visible web UI interactions." },
      { label: "UI + API docs", description: "Use Swagger to discover features, then write UI-testable scenarios." },
      { label: "Technical observable", description: "Observable security, data, and integration behavior without code or DB access." },
    ],
  },
  {
    title: "Scenario Level",
    description: "How broad each scenario should be.",
    options: [
      { label: "HLS", description: "One tester intent with a clear outcome." },
      { label: "Detailed HLS", description: "Adds context and constraints without becoming test steps." },
    ],
  },
  {
    title: "Scenario Types",
    description: "Which scenario styles the agents should focus on.",
    options: [
      { label: "ALL", description: "Any document-backed type." },
      { label: "HLS", description: "General high-level workflows." },
      { label: "Functional", description: "Create, update, submit, approve, search, and view flows." },
      { label: "Technical", description: "Observable technical behavior without code or DB access." },
      { label: "API", description: "API-discovered capabilities expressed as tester scenarios." },
      { label: "Security", description: "Login, permissions, sessions, and protected actions." },
      { label: "Performance", description: "Responsiveness, load, and large-data behavior." },
      { label: "Integration", description: "Upload, export, import, notifications, and external flows." },
      { label: "Data", description: "Validation, persistence, filtering, and displayed correctness." },
      { label: "Compliance", description: "Policy, audit, consent, and rule-driven behavior." },
      { label: "Usability", description: "Navigation, feedback, form clarity, and interaction quality." },
    ],
  },
];

function clampScenarioCount(value: string) {
  const digitsOnly = value.replace(/\D/g, "");
  if (!digitsOnly) return "";
  const numericValue = Number(digitsOnly);
  if (numericValue < 1) return "1";
  if (numericValue > 500) return "500";
  return String(numericValue);
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function sourceBadge(source: ScenarioSource) {
  if (source === "agent_1") {
    return { label: "Agent 1", className: "bg-blue-50 text-blue-700 border-blue-200" };
  }
  if (source === "agent_2") {
    return { label: "Agent 2", className: "bg-purple-50 text-purple-700 border-purple-200" };
  }
  return { label: "Manual", className: "bg-zinc-100 text-zinc-700 border-zinc-200" };
}

function scenarioTags(source: ScenarioSource) {
  return [sourceBadge(source).label];
}

function recordingFailureText(reasons: string[]) {
  if (!reasons.length) return "Quality gate failed";
  return reasons.map((reason) => reason.replace(/_/g, " ")).join(", ");
}

function recordingBadge(scenario: HighLevelScenario) {
  if (!scenario.recording_status) {
    return {
      label: "Not recorded",
      title: "No recording session has been saved yet.",
      className: "border-zinc-200 bg-zinc-50 text-zinc-600",
    };
  }
  if (scenario.recording_status === "in_progress") {
    return {
      label: "Recording",
      title: "Recording is currently in progress.",
      className: "border-red-200 bg-red-50 text-red-700",
    };
  }
  if (scenario.recording_status === "failed") {
    return {
      label: "Recording failed",
      title: "The latest recording session failed.",
      className: "border-red-200 bg-red-50 text-red-700",
    };
  }
  if (scenario.recording_status === "completed" && scenario.recording_phase3_ready === false) {
    return {
      label: "Needs re-recording",
      title: recordingFailureText(scenario.recording_quality_failure_reasons),
      className: "border-amber-200 bg-amber-50 text-amber-800",
    };
  }
  if (scenario.recording_status === "completed" && scenario.recording_phase3_ready === true) {
    return {
      label: "Phase 3 ready",
      title: `${scenario.recording_step_count} steps recorded.`,
      className: "border-emerald-200 bg-emerald-50 text-emerald-700",
    };
  }
  return {
    label: "Recording pending",
    title: "Recording session has been created but not started.",
    className: "border-zinc-200 bg-zinc-100 text-zinc-700",
  };
}

function tableInputClass() {
  return "min-h-10 w-full rounded-md border border-black/20 bg-white px-3 py-2 text-sm text-black focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2a63f5]";
}

export function ScenarioQaPanel({ projectId, currentUserId }: ScenarioQaPanelProps) {
  const [approvedScenarios, setApprovedScenarios] = useState<HighLevelScenario[]>([]);
  const [previewScenarios, setPreviewScenarios] = useState<PreviewScenario[]>([]);
  const [isLoadingApproved, setIsLoadingApproved] = useState(true);
  const [isGenerating, setIsGenerating] = useState(false);
  const [generationLogs, setGenerationLogs] = useState<string[]>([]);
  const [previewEditIndex, setPreviewEditIndex] = useState<number | null>(null);
  const [isApproving, setIsApproving] = useState(false);
  const [previewDraft, setPreviewDraft] = useState<DraftScenario>({ title: "", description: "" });
  const [approvedEditId, setApprovedEditId] = useState<string | null>(null);
  const [approvedDraft, setApprovedDraft] = useState<DraftScenario>({ title: "", description: "" });
  const [approvedSheetFilter, setApprovedSheetFilter] = useState<string>("all");
  const [isAddingApproved, setIsAddingApproved] = useState(false);
  const [newApprovedDraft, setNewApprovedDraft] = useState<DraftScenario>({ title: "", description: "" });
  const [isLoadingXlsx, setIsLoadingXlsx] = useState(false);
  const [testDocumentName, setTestDocumentName] = useState<string | null>(null);
  const [testDocumentSheets, setTestDocumentSheets] = useState<TestDocumentSheet[]>([]);
  const [selectedSheetName, setSelectedSheetName] = useState<string>("");
  // The uploaded test document file currently being previewed, and the one whose
  // rows were last fully approved. The preview shows whenever the current file
  // differs from the approved one — so a freshly uploaded document (even with
  // identical rows) reappears, while the already-approved document stays hidden
  // after approval or a tab switch.
  const [currentDocumentFileId, setCurrentDocumentFileId] = useState<string | null>(null);
  const [approvedDocumentFileId, setApprovedDocumentFileId] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    try {
      return window.localStorage.getItem(`sqat:qa:${projectId}:approved-file-id`);
    } catch {
      return null;
    }
  });
  const [scenarioLimit, setScenarioLimit] = useState<(typeof SCENARIO_LIMIT_OPTIONS)[number]["value"]>("20");
  const [customScenarioCount, setCustomScenarioCount] = useState("35");
  const [scenarioTypes, setScenarioTypes] = useState<ScenarioGenerationType[]>(["ALL"]);
  const [accessMode, setAccessMode] = useState<ScenarioAccessMode>("UI_ONLY_WEB");
  const [scenarioLevel, setScenarioLevel] = useState<ScenarioLevel>("HLS");
  const [isOptionsGuideOpen, setIsOptionsGuideOpen] = useState(false);
  const [expandedDescriptionIds, setExpandedDescriptionIds] = useState<string[]>([]);
  // Trigger / Launch state
  const [launchingScenarioId, setLaunchingScenarioId] = useState<string | null>(null);
  const [recordingScenarioId, setRecordingScenarioId] = useState<string | null>(null);
  const [recordingSessionStatus, setRecordingSessionStatus] = useState<string>("none");
  const [clearingRecordingScenarioId, setClearingRecordingScenarioId] = useState<string | null>(null);
  // Setup Recorder popover
  const [showSetupModal, setShowSetupModal] = useState(false);
  const [setupTab, setSetupTab] = useState<"mac" | "windows">("mac");
  const [setupInfo, setSetupInfo] = useState<RecordingSetupResponse | null>(null);
  const [isLoadingSetup, setIsLoadingSetup] = useState(false);
  const [copiedSetup, setCopiedSetup] = useState(false);

  const loadApprovedScenarios = useCallback(async () => {
    setIsLoadingApproved(true);
    try {
      const response = await listHighLevelScenarios(projectId);
      setApprovedScenarios(response.scenarios);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Unable to load scenarios.");
    } finally {
      setIsLoadingApproved(false);
    }
  }, [projectId]);

  const approvedSheetOptions = Array.from(
    new Set(approvedScenarios.map((scenario) => scenario.sheet_name ?? "Unassigned")),
  ).sort((a, b) => a.localeCompare(b));

  const visibleApprovedScenarios =
    approvedSheetFilter === "all"
      ? approvedScenarios
      : approvedScenarios.filter((scenario) => (scenario.sheet_name ?? "Unassigned") === approvedSheetFilter);

  // Scenario Preview should only surface rows from a test document that has not
  // yet been approved. Approval is keyed to the uploaded file itself — not the
  // row content — so a newly uploaded document always shows its preview again,
  // even when its rows duplicate ones already approved from a previous upload.
  const showScenarioPreview =
    isLoadingXlsx || (currentDocumentFileId !== null && currentDocumentFileId !== approvedDocumentFileId);

  useEffect(() => {
    if (approvedSheetFilter !== "all" && !approvedSheetOptions.includes(approvedSheetFilter)) {
      setApprovedSheetFilter("all");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [approvedSheetOptions.length]);

  const loadTestDocumentRows = useCallback(async () => {
    setIsLoadingXlsx(true);
    try {
      const response = await fetchTestDocumentRows(projectId);
      setCurrentDocumentFileId(response.file?.id ?? null);
      if (response.sheets.length > 0) {
        setTestDocumentSheets(response.sheets);
        setSelectedSheetName(response.sheets[0].name);
        setPreviewScenarios(response.sheets[0].items.map((row) => mapTestDocumentRow(row, response.sheets[0].name)));
        setTestDocumentName(response.file?.original_filename ?? null);
      } else {
        setTestDocumentSheets([]);
        setSelectedSheetName("");
        setPreviewScenarios([]);
        setTestDocumentName(null);
      }
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Unable to read the test document.");
      setTestDocumentSheets([]);
      setSelectedSheetName("");
      setPreviewScenarios([]);
      setTestDocumentName(null);
    } finally {
      setIsLoadingXlsx(false);
    }
  }, [projectId]);

  const handleSheetChange = (sheetName: string) => {
    setSelectedSheetName(sheetName);
    const sheet = testDocumentSheets.find((candidate) => candidate.name === sheetName);
    setPreviewScenarios(sheet ? sheet.items.map((row) => mapTestDocumentRow(row, sheetName)) : []);
    setPreviewEditIndex(null);
  };

  const handleLaunch = async (scenario: HighLevelScenario) => {
    setLaunchingScenarioId(scenario.id);
    setRecordingSessionStatus("none");
    try {
      await triggerScenarioLaunch(projectId, scenario.id);
      setRecordingScenarioId(scenario.id);
      toast.success(`Launch triggered — waiting for daemon to pick it up...`);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Failed to trigger launch.");
      setRecordingSessionStatus("none");
      setRecordingScenarioId(null);
    } finally {
      setLaunchingScenarioId(null);
    }
  };

  const handleOpenSetup = async () => {
    setShowSetupModal(true);
    if (setupInfo) return; // already loaded
    setIsLoadingSetup(true);
    try {
      const info = await getRecordingSetup(projectId);
      setSetupInfo(info);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Failed to load setup info.");
    } finally {
      setIsLoadingSetup(false);
    }
  };

  const handleCopySetup = () => {
    if (!setupInfo) return;
    const cmd = setupTab === "mac" ? setupInfo.mac_setup_command : setupInfo.windows_setup_command;
    void navigator.clipboard.writeText(cmd);
    setCopiedSetup(true);
    setTimeout(() => setCopiedSetup(false), 2000);
  };

  useEffect(() => {
    void loadApprovedScenarios();
    void loadTestDocumentRows();
  }, [loadApprovedScenarios, loadTestDocumentRows]);

  // Poll recording status while a scenario is being recorded
  useEffect(() => {
    if (!recordingScenarioId) return;
    let hasStarted = false;
    const interval = setInterval(async () => {
      try {
        const { session_status, phase3_ready, quality_failure_reasons } = await getScenarioRecordingStatus(projectId, recordingScenarioId);
        setRecordingSessionStatus(session_status);
        if (session_status === "in_progress") {
          hasStarted = true;
        } else if (hasStarted && (session_status === "completed" || session_status === "failed")) {
          setRecordingScenarioId(null);
          setRecordingSessionStatus("none");
          await loadApprovedScenarios();
          if (session_status === "completed" && !phase3_ready) {
            toast.warning(`Recording saved, but quality needs review: ${recordingFailureText(quality_failure_reasons)}`);
            return;
          }
          if (session_status === "completed") {
            toast.success("Recording finished — session saved.");
          } else {
            toast.warning("Recording session ended with errors.");
          }
        }
      } catch {
        // silently ignore poll errors
      }
    }, 2500);
    return () => clearInterval(interval);
  }, [recordingScenarioId, projectId, loadApprovedScenarios]);

  const startPreviewEdit = (index: number) => {
    const scenario = previewScenarios[index];
    setPreviewEditIndex(index);
    setPreviewDraft({
      title: scenario.title,
      description: scenario.description,
      test_id: scenario.test_id ?? "",
      pre_conditions: scenario.pre_conditions ?? "",
      test_steps: scenario.test_steps ?? "",
      expected_result: scenario.expected_result ?? "",
    });
  };

  const savePreviewEdit = () => {
    if (previewEditIndex === null) return;
    if (!previewDraft.title.trim()) {
      toast.error("Scenario title is required.");
      return;
    }
    const description = buildDescriptionFromDraft(previewDraft);
    setPreviewScenarios((current) =>
      current.map((scenario, index) =>
        index === previewEditIndex
          ? {
              ...scenario,
              title: previewDraft.title.trim(),
              description,
              test_id: previewDraft.test_id?.trim() || scenario.test_id,
              pre_conditions: previewDraft.pre_conditions ?? "",
              test_steps: previewDraft.test_steps ?? "",
              expected_result: previewDraft.expected_result ?? "",
            }
          : scenario,
      ),
    );
    setPreviewEditIndex(null);
  };

  const handleGenerate = async () => {
    setIsGenerating(true);
    setGenerationLogs([]);
    const maxScenarios =
      scenarioLimit === "max"
        ? null
        : scenarioLimit === "custom"
          ? Number(customScenarioCount || "1")
          : Number(scenarioLimit);

    const existingScenarios: PreviewScenario[] = [
      ...approvedScenarios.map((scenario) => ({
        title: scenario.title,
        description: scenario.description,
        source: scenario.source,
      })),
      ...previewScenarios,
    ];

    try {
      const response = await generateHighLevelScenariosStream(
        projectId,
        {
          max_scenarios: maxScenarios,
          scenario_types: scenarioTypes,
          access_mode: accessMode,
          scenario_level: scenarioLevel,
          existing_scenarios: existingScenarios,
        },
        (msg) => {
          setGenerationLogs((current) => [...current, msg]);
        }
      );
      
      setPreviewScenarios((current) => [...current, ...response.scenarios]);
      if (response.scenarios.length === 0) {
        toast.info(existingScenarios.length > 0 ? "No additional scenarios were found." : "No scenarios were generated from the ingested chunks.");
      } else {
        toast.success(`Generated ${response.scenarios.length} additional high level scenarios.`);
      }
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : error instanceof Error ? error.message : "Scenario generation failed.");
    } finally {
      setIsGenerating(false);
    }
  };

  const toggleScenarioType = (type: ScenarioGenerationType) => {
    setScenarioTypes((current) => {
      if (type === "ALL") return ["ALL"];
      const withoutAll = current.filter((item) => item !== "ALL");
      const next = withoutAll.includes(type)
        ? withoutAll.filter((item) => item !== type)
        : [...withoutAll, type];
      return next.length > 0 ? next : ["ALL"];
    });
  };

  const toggleApprovedDescription = (scenarioId: string) => {
    setExpandedDescriptionIds((current) =>
      current.includes(scenarioId)
        ? current.filter((id) => id !== scenarioId)
        : [...current, scenarioId],
    );
  };

  const handleAddPreviewScenario = () => {
    setPreviewScenarios((current) => [...current, { title: "", description: "", source: "manual", pre_conditions: "", test_steps: "", expected_result: "", sheet_name: selectedSheetName || null }]);
    setPreviewEditIndex(previewScenarios.length);
    setPreviewDraft({ title: "", description: "", pre_conditions: "", test_steps: "", expected_result: "" });
  };

  const handleApprove = async () => {
    const allScenarios: PreviewScenario[] = [];
    testDocumentSheets.forEach((sheet) => {
      if (sheet.name === selectedSheetName) {
        allScenarios.push(...previewScenarios);
      } else {
        allScenarios.push(...sheet.items.map((row) => mapTestDocumentRow(row, sheet.name)));
      }
    });

    const clean = allScenarios
      .map((scenario) => ({
        ...scenario,
        title: scenario.title.trim(),
        description: scenario.description.trim(),
      }))
      .filter((scenario) => scenario.title);

    if (clean.length === 0) {
      toast.error("Add at least one scenario before approving.");
      return;
    }

    setIsApproving(true);
    try {
      const response = await approveHighLevelScenarios(projectId, clean);
      if (currentDocumentFileId) {
        setApprovedDocumentFileId(currentDocumentFileId);
        try {
          window.localStorage.setItem(`sqat:qa:${projectId}:approved-file-id`, currentDocumentFileId);
        } catch {
          // ignore storage failures
        }
      }
      setPreviewScenarios([]);
      setTestDocumentSheets((current) => current.map((sheet) => ({ ...sheet, items: [] })));
      await loadApprovedScenarios();
      toast.success(`Saved ${response.saved} scenarios across ${testDocumentSheets.length} sheet(s).`);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Unable to save scenarios.");
    } finally {
      setIsApproving(false);
    }
  };

  const startApprovedEdit = (scenario: HighLevelScenario) => {
    setApprovedEditId(scenario.id);
    setApprovedDraft({
      title: scenario.title,
      description: scenario.description,
      test_id: scenario.test_id ?? "",
      sheet_name: scenario.sheet_name ?? "",
      pre_conditions: scenario.pre_conditions ?? "",
      test_steps: scenario.test_steps ?? "",
      expected_result: scenario.expected_result ?? "",
    });
  };

  const saveApprovedEdit = async (scenario: HighLevelScenario) => {
    if (!approvedDraft.title.trim()) {
      toast.error("Scenario title is required.");
      return;
    }
    try {
      const updated = await updateHighLevelScenario(projectId, scenario.id, {
        title: approvedDraft.title.trim(),
        description: buildDescriptionFromDraft(approvedDraft),
        test_id: approvedDraft.test_id?.trim() || null,
        sheet_name: approvedDraft.sheet_name?.trim() || null,
        pre_conditions: approvedDraft.pre_conditions?.trim() || null,
        test_steps: approvedDraft.test_steps?.trim() || null,
        expected_result: approvedDraft.expected_result?.trim() || null,
      });
      setApprovedScenarios((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setApprovedEditId(null);
      toast.success("Scenario updated.");
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Unable to update scenario.");
    }
  };

  const toggleStatus = async (scenario: HighLevelScenario) => {
    const nextStatus = scenario.status === "completed" ? "pending" : "completed";
    if (nextStatus === "completed" && !currentUserId) {
      toast.error("Current user is required to complete a scenario.");
      return;
    }
    try {
      const updated = await updateHighLevelScenario(projectId, scenario.id, {
        status: nextStatus,
        ...(nextStatus === "completed" ? { current_user_id: currentUserId ?? undefined } : {}),
      });
      setApprovedScenarios((current) => current.map((item) => (item.id === updated.id ? updated : item)));
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Unable to update status.");
    }
  };

  const deleteApproved = async (scenario: HighLevelScenario) => {
    if (!window.confirm(`Delete scenario "${scenario.title}"?`)) return;
    try {
      await deleteHighLevelScenario(projectId, scenario.id);
      setApprovedScenarios((current) => current.filter((item) => item.id !== scenario.id));
      toast.success("Scenario deleted.");
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Unable to delete scenario.");
    }
  };

  const clearApprovedRecording = async (scenario: HighLevelScenario) => {
    if (
      !window.confirm(
        `Clear all web recording data for "${scenario.title}"? This removes recorded steps, screenshots, route snapshots, and recording files.`,
      )
    ) {
      return;
    }

    setClearingRecordingScenarioId(scenario.id);
    try {
      const result = await clearScenarioRecording(projectId, scenario.id);
      if (recordingScenarioId === scenario.id) {
        setRecordingScenarioId(null);
      }
      await loadApprovedScenarios();
      toast.success(
        `Recording cleared. Removed ${result.steps_deleted} steps, ${result.route_variants_deleted} route snapshots, and ${result.files_deleted} files.`,
      );
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Unable to clear recording.");
    } finally {
      setClearingRecordingScenarioId(null);
    }
  };

  const createApproved = async () => {
    if (!newApprovedDraft.title.trim()) {
      toast.error("Scenario title is required.");
      return;
    }
    try {
      const saved = await createHighLevelScenario(projectId, {
        title: newApprovedDraft.title.trim(),
        description: buildDescriptionFromDraft(newApprovedDraft),
        test_id: newApprovedDraft.test_id?.trim() || null,
        sheet_name: newApprovedDraft.sheet_name?.trim() || null,
        pre_conditions: newApprovedDraft.pre_conditions?.trim() || null,
        test_steps: newApprovedDraft.test_steps?.trim() || null,
        expected_result: newApprovedDraft.expected_result?.trim() || null,
      });
      setApprovedScenarios((current) => [...current, saved]);
      setNewApprovedDraft({ title: "", description: "", test_id: "", sheet_name: null, pre_conditions: "", test_steps: "", expected_result: "" });
      setIsAddingApproved(false);
      toast.success("Scenario added.");
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Unable to add scenario.");
    }
  };

  const generationSettingsCard = (
    <div className="rounded-lg border border-black/10 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-black">High Level Scenarios Configuration</h2>
          <p className="text-sm text-black/60">Generate focused tester scenarios from ingested project documents.</p>
        </div>
        <Button onClick={() => void handleGenerate()} disabled={isGenerating} className="gap-2">
          {isGenerating ? <Loader2 className="h-4 w-4 animate-spin" /> : <WandSparkles className="h-4 w-4" />}
          {isGenerating ? "Generating..." : approvedScenarios.length > 0 || previewScenarios.length > 0 ? "Generate More Scenarios" : "Generate High Level Scenarios"}
        </Button>
      </div>

      <div className="mt-4 flex flex-wrap items-start gap-x-8 gap-y-4 border-t border-black/10 pt-4">
        <div className="shrink-0">
          <p className="text-xs font-semibold uppercase text-black/60">Scenario Range</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {SCENARIO_LIMIT_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => setScenarioLimit(option.value)}
                disabled={isGenerating}
                className={cn(
                  "flex h-9 min-w-24 items-center justify-center rounded-md border px-3 text-sm font-medium transition-colors",
                  scenarioLimit === option.value
                    ? "border-[#2a63f5] bg-[#2a63f5] text-white"
                    : "border-black/15 bg-white text-black hover:bg-[#2a63f5]/5",
                )}
              >
                {option.label}
              </button>
            ))}
            {scenarioLimit === "custom" ? (
              <Input
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                value={customScenarioCount}
                onChange={(event) => setCustomScenarioCount(clampScenarioCount(event.target.value))}
                onBlur={() => setCustomScenarioCount((current) => current || "1")}
                disabled={isGenerating}
                className="h-9 w-28 rounded-md [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
                aria-label="Custom scenario count"
              />
            ) : null}
          </div>
        </div>

        <div className="shrink-0">
          <p className="text-xs font-semibold uppercase text-black/60">Tester Access</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {ACCESS_MODE_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => setAccessMode(option.value)}
                disabled={isGenerating}
                className={cn(
                  "flex h-9 min-w-36 items-center justify-center rounded-md border px-3 text-sm font-medium transition-colors",
                  accessMode === option.value
                    ? "border-[#2a63f5] bg-[#2a63f5] text-white"
                    : "border-black/15 bg-white text-black hover:bg-[#2a63f5]/5",
                )}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>

        <div className="shrink-0">
          <p className="text-xs font-semibold uppercase text-black/60">Scenario Level</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {SCENARIO_LEVEL_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => setScenarioLevel(option.value)}
                disabled={isGenerating}
                className={cn(
                  "flex h-9 min-w-28 items-center justify-center rounded-md border px-3 text-sm font-medium transition-colors",
                  scenarioLevel === option.value
                    ? "border-[#2a63f5] bg-[#2a63f5] text-white"
                    : "border-black/15 bg-white text-black hover:bg-[#2a63f5]/5",
                )}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>

        <div className="min-w-[320px] flex-1">
          <p className="text-xs font-semibold uppercase text-black/60">Scenario Types</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {SCENARIO_TYPE_OPTIONS.map((type) => {
              const isSelected = scenarioTypes.includes(type);
              return (
                <button
                  key={type}
                  type="button"
                  onClick={() => toggleScenarioType(type)}
                  disabled={isGenerating}
                  className={cn(
                    "flex h-9 min-w-20 items-center justify-center rounded-md border px-3 text-sm font-medium transition-colors",
                    isSelected
                      ? "border-[#2a63f5] bg-[#2a63f5] text-white"
                      : "border-black/15 bg-white text-black hover:bg-[#2a63f5]/5",
                  )}
                >
                  {type}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <div className="mt-4 border-t border-black/10 pt-3">
        <button
          type="button"
          onClick={() => setIsOptionsGuideOpen((current) => !current)}
          className="inline-flex items-center gap-2 text-sm font-medium text-black/70 hover:text-black"
        >
          <Info className="h-4 w-4 text-[#2a63f5]" />
          Option guide
          {isOptionsGuideOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </button>
        {isOptionsGuideOpen ? (
          <div className="mt-3 rounded-md border border-black/10 bg-black/[0.02] px-3">
            {OPTION_GUIDE.map((item) => (
              <details key={item.title} className="group border-t border-black/10 first:border-t-0">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-4 py-3 [&::-webkit-details-marker]:hidden">
                  <div className="min-w-0">
                    <p className="text-xs font-semibold uppercase text-black/55">{item.title}</p>
                    <p className="mt-0.5 text-sm leading-5 text-black/65">{item.description}</p>
                  </div>
                  <ChevronDown className="h-4 w-4 shrink-0 text-black/45 transition-transform group-open:rotate-180" />
                </summary>
                {item.options ? (
                  <div className="grid gap-x-4 gap-y-2 pb-3 pt-1 sm:grid-cols-2 xl:grid-cols-3">
                    {item.options.map((option) => (
                      <div key={option.label} className="rounded-md bg-white px-3 py-2 text-sm leading-5">
                        <span className="font-semibold text-black">{option.label}</span>
                        <span className="block text-black/65">{option.description}</span>
                      </div>
                    ))}
                  </div>
                ) : null}
              </details>
            ))}
          </div>
        ) : null}
      </div>

      {isGenerating || generationLogs.length > 0 ? (
        <div className="mt-4 rounded-md border border-black/10 bg-black/[0.02] p-3 text-xs text-black/70 font-mono">
          <div className="flex items-center gap-2 mb-2 pb-2 border-b border-black/5 font-sans font-medium text-black">
            {isGenerating ? <Loader2 className="h-4 w-4 animate-spin text-[#2a63f5]" /> : <Check className="h-4 w-4 text-emerald-600" />}
            {isGenerating ? "Generation in progress..." : "Generation complete"}
          </div>
          <div className="max-h-48 overflow-y-auto space-y-1">
            {generationLogs.map((log, i) => (
              <div key={i}>{log}</div>
            ))}
            {isGenerating && generationLogs.length === 0 && (
              <div className="italic text-black/40">Waiting for agent cluster to spin up...</div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );

  if (isLoadingApproved) {
    return (
      <div className="flex min-h-44 items-center justify-center rounded-lg border border-black/10 bg-white">
        <Loader2 className="h-5 w-5 animate-spin text-[#2a63f5]" />
      </div>
    );
  }

  return (
    <>
      <div className="space-y-6">
      {/* High Level Scenarios Configuration + Generate — temporarily commented out.
          The QA tab now reads scenarios from the uploaded xlsx test document instead.
          Re-enable generation later by uncommenting the line below.
          {generationSettingsCard} */}

      {showScenarioPreview ? (
        <div className="overflow-hidden rounded-lg border border-black/10 bg-white">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-black/10 px-4 py-3">
            <div>
              <h2 className="text-base font-semibold text-black">Scenario Preview</h2>
              <p className="text-sm text-black/60">
                {isLoadingXlsx ? "Reading test document..." : testDocumentName ? `Loaded from "${testDocumentName}". Edits stay in memory until approval.` : "Edits here stay in memory until approval."}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              {testDocumentSheets.length > 1 ? (
                <div className="flex items-center gap-2">
                  <label htmlFor="test-sheet-select" className="text-xs font-medium uppercase text-black/60">
                    Sheet
                  </label>
                  <select
                    id="test-sheet-select"
                    value={selectedSheetName}
                    onChange={(event) => handleSheetChange(event.target.value)}
                    className="h-9 max-w-72 rounded-md border border-black/15 bg-white px-3 text-sm font-medium text-black focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2a63f5]"
                  >
                    {testDocumentSheets.map((sheet) => (
                      <option key={sheet.name} value={sheet.name}>
                        {sheet.name} ({sheet.items.length})
                      </option>
                    ))}
                  </select>
                </div>
              ) : null}
              <Button variant="outline" onClick={handleAddPreviewScenario}>
                <Plus className="h-4 w-4" />
                Add Scenario Manually
              </Button>
              <Button onClick={() => void handleApprove()} disabled={isApproving}>
                {isApproving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                Approve & Save
              </Button>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1600px] table-fixed text-left text-sm">
              <thead className="bg-black/[0.03] text-xs uppercase text-black/60">
                <tr>
                  <th className="w-10 px-3 py-3">#</th>
                  <th className="w-[110px] px-3 py-3">Test ID</th>
                  <th className="w-[260px] px-3 py-3">Test Scenario</th>
                  <th className="w-[240px] px-3 py-3">Pre-Conditions</th>
                  <th className="w-[260px] px-3 py-3">Test Steps</th>
                  <th className="w-[300px] px-3 py-3">Expected Result</th>
                  <th className="w-[150px] px-3 py-3">Tags</th>
                  <th className="w-[130px] px-3 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {previewScenarios.map((scenario, index) => {
                  const isEditing = previewEditIndex === index;
                  return (
                    <tr key={`${scenario.source}-${index}`}>
                      <td className="px-3 py-3 text-black/60">{index + 1}</td>
                      <td className="px-3 py-3">
                        <span className="block truncate font-mono text-xs text-black/70" title={scenario.test_id}>
                          {scenario.test_id || "-"}
                        </span>
                      </td>
                      <td className="px-3 py-3">
                        {isEditing ? (
                          <Input value={previewDraft.title} onChange={(e) => setPreviewDraft((current) => ({ ...current, title: e.target.value }))} />
                        ) : (
                          <span className="block font-medium leading-6 text-black">{scenario.title}</span>
                        )}
                      </td>
                      <td className="px-3 py-3">
                        {isEditing ? (
                          <textarea
                            rows={3}
                            value={previewDraft.pre_conditions ?? ""}
                            onChange={(e) => setPreviewDraft((current) => ({ ...current, pre_conditions: e.target.value }))}
                            className={tableInputClass()}
                          />
                        ) : (
                          <span className="block whitespace-pre-line leading-6 text-black/70">{scenario.pre_conditions || "—"}</span>
                        )}
                      </td>
                      <td className="px-3 py-3">
                        {isEditing ? (
                          <textarea
                            rows={3}
                            value={previewDraft.test_steps ?? ""}
                            onChange={(e) => setPreviewDraft((current) => ({ ...current, test_steps: e.target.value }))}
                            className={tableInputClass()}
                          />
                        ) : (
                          <span className="block whitespace-pre-line leading-6 text-black/70">{scenario.test_steps || "—"}</span>
                        )}
                      </td>
                      <td className="px-3 py-3">
                        {isEditing ? (
                          <textarea
                            rows={3}
                            value={previewDraft.expected_result ?? ""}
                            onChange={(e) => setPreviewDraft((current) => ({ ...current, expected_result: e.target.value }))}
                            className={tableInputClass()}
                          />
                        ) : (
                          <span className="block whitespace-pre-line leading-6 text-black/70">{scenario.expected_result || "—"}</span>
                        )}
                      </td>
                      <td className="px-3 py-3">
                        <div className="flex flex-wrap gap-1.5">
                          {scenarioTags(scenario.source).map((tag) => (
                            <span key={tag} className="inline-flex rounded-full border border-[#2a63f5]/20 bg-[#2a63f5]/5 px-2 py-1 text-xs font-semibold text-[#2a63f5]">
                              {tag}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td className="px-3 py-3">
                        <div className="flex justify-end gap-2">
                          {isEditing ? (
                            <>
                              <Button size="sm" onClick={savePreviewEdit}>
                                <Check className="h-4 w-4" />
                                Save
                              </Button>
                              <Button size="sm" variant="outline" onClick={() => setPreviewEditIndex(null)}>
                                <X className="h-4 w-4" />
                                Cancel
                              </Button>
                            </>
                          ) : (
                            <>
                              <Button size="icon" variant="outline" onClick={() => startPreviewEdit(index)} aria-label={`Edit ${scenario.title}`} title="Edit">
                                <Pencil className="h-4 w-4" />
                              </Button>
                              <Button
                                size="icon"
                                variant="outline"
                                className="border-red-200 text-red-600 hover:bg-red-50"
                                onClick={() => setPreviewScenarios((current) => current.filter((_, itemIndex) => itemIndex !== index))}
                                aria-label={`Delete ${scenario.title}`}
                                title="Delete"
                              >
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {approvedScenarios.length > 0 ? (
        <div className="overflow-hidden rounded-lg border border-black/10 bg-white">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-black/10 px-4 py-3">
            <div>
              <h2 className="text-base font-semibold text-black">Approved Scenarios</h2>
              <p className="text-sm text-black/60">
                {approvedSheetFilter === "all"
                  ? `${approvedScenarios.length} scenarios ready for tester review.`
                  : `${visibleApprovedScenarios.length} of ${approvedScenarios.length} scenarios in "${approvedSheetFilter}".`}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              {approvedSheetOptions.length > 0 ? (
                <div className="flex items-center gap-2">
                  <label htmlFor="approved-sheet-select" className="text-xs font-medium uppercase text-black/60">
                    Sheet
                  </label>
                  <select
                    id="approved-sheet-select"
                    value={approvedSheetFilter}
                    onChange={(event) => setApprovedSheetFilter(event.target.value)}
                    className="h-9 max-w-72 rounded-md border border-black/15 bg-white px-3 text-sm font-medium text-black focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2a63f5]"
                  >
                    <option value="all">All sheets</option>
                    {approvedSheetOptions.map((sheet) => (
                      <option key={sheet} value={sheet}>
                        {sheet}
                      </option>
                    ))}
                  </select>
                </div>
              ) : null}
              <Button variant="outline" onClick={() => void handleOpenSetup()}>
                <Info className="h-4 w-4" />
                Setup Recorder
              </Button>
              <Button
                variant="outline"
                onClick={() => {
                  setNewApprovedDraft({
                    title: "",
                    description: "",
                    sheet_name: approvedSheetFilter !== "all" ? approvedSheetFilter : null,
                    pre_conditions: "",
                    test_steps: "",
                    expected_result: "",
                  });
                  setIsAddingApproved(true);
                }}
              >
                <Plus className="h-4 w-4" />
                Add Scenario
              </Button>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1900px] table-fixed text-left text-sm">
              <thead className="bg-black/[0.03] text-xs uppercase text-black/60">
                <tr>
                  <th className="w-[110px] px-3 py-3">Test ID</th>
                  <th className="w-[150px] px-3 py-3">Sheet</th>
                  <th className="w-[300px] px-3 py-3">Test Scenario</th>
                  <th className="w-[220px] px-3 py-3">Pre-Conditions</th>
                  <th className="w-[240px] px-3 py-3">Test Steps</th>
                  <th className="w-[260px] px-3 py-3">Expected Result</th>
                  <th className="w-[120px] px-3 py-3">Tags</th>
                  <th className="w-[130px] px-3 py-3">Status</th>
                  <th className="w-[130px] px-3 py-3 text-right">Actions</th>
                  <th className="w-[230px] px-3 py-3 text-right">Launch</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-black/10">
                {visibleApprovedScenarios.map((scenario) => {
                  const isEditing = approvedEditId === scenario.id;
                  const isDescriptionOpen = expandedDescriptionIds.includes(scenario.id);
                  const recorderBadge = recordingBadge(scenario);
                  const sheetName = scenario.sheet_name ?? "Unassigned";
                  return (
                    <Fragment key={scenario.id}>
                      <tr className={cn("border-t border-black/10", isDescriptionOpen ? "bg-[#2a63f5]/[0.03]" : undefined)}>
                        <td className="px-3 py-3">
                          {isEditing ? (
                            <Input
                              value={approvedDraft.test_id ?? ""}
                              onChange={(e) => setApprovedDraft((current) => ({ ...current, test_id: e.target.value }))}
                              placeholder="TC-0001"
                              className="font-mono text-xs"
                            />
                          ) : (
                            <span className="block truncate font-mono text-xs text-black/70" title={scenario.test_id ?? ""}>
                              {scenario.test_id || "—"}
                            </span>
                          )}
                        </td>
                        <td className="px-3 py-3">
                          {isEditing ? (
                            <select
                              value={approvedDraft.sheet_name ?? "Unassigned"}
                              onChange={(e) => {
                                const value = e.target.value;
                                setApprovedDraft((current) => ({ ...current, sheet_name: value === "Unassigned" ? null : value }));
                              }}
                              className="h-9 w-full rounded-md border border-black/15 bg-white px-3 text-sm font-medium text-black focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2a63f5]"
                            >
                              <option value="Unassigned">Unassigned</option>
                              {Array.from(
                                new Set([...approvedSheetOptions, ...testDocumentSheets.map((sheet) => sheet.name)]),
                              )
                                .filter((sheet) => sheet !== "Unassigned")
                                .map((sheet) => (
                                  <option key={sheet} value={sheet}>
                                    {sheet}
                                  </option>
                                ))}
                            </select>
                          ) : (
                            <span className="block truncate rounded-full border border-black/10 bg-black/[0.03] px-2 py-1 text-xs font-semibold text-black/70" title={sheetName}>
                              {sheetName}
                            </span>
                          )}
                        </td>
                        <td className="px-3 py-3">
                          {isEditing ? (
                            <Input value={approvedDraft.title} onChange={(e) => setApprovedDraft((current) => ({ ...current, title: e.target.value }))} />
                          ) : (
                            <span className="block font-medium leading-6 text-black">{scenario.title}</span>
                          )}
                        </td>
                        <td className="px-3 py-3">
                          {isEditing ? (
                            <textarea
                              rows={3}
                              value={approvedDraft.pre_conditions ?? ""}
                              onChange={(e) => setApprovedDraft((current) => ({ ...current, pre_conditions: e.target.value }))}
                              className={tableInputClass()}
                            />
                          ) : (
                            <span className="block whitespace-pre-line leading-6 text-black/70">{scenario.pre_conditions || "—"}</span>
                          )}
                        </td>
                        <td className="px-3 py-3">
                          {isEditing ? (
                            <textarea
                              rows={3}
                              value={approvedDraft.test_steps ?? ""}
                              onChange={(e) => setApprovedDraft((current) => ({ ...current, test_steps: e.target.value }))}
                              className={tableInputClass()}
                            />
                          ) : (
                            <span className="block whitespace-pre-line leading-6 text-black/70">{scenario.test_steps || "—"}</span>
                          )}
                        </td>
                        <td className="px-3 py-3">
                          {isEditing ? (
                            <textarea
                              rows={3}
                              value={approvedDraft.expected_result ?? ""}
                              onChange={(e) => setApprovedDraft((current) => ({ ...current, expected_result: e.target.value }))}
                              className={tableInputClass()}
                            />
                          ) : (
                            <span className="block whitespace-pre-line leading-6 text-black/70">{scenario.expected_result || "—"}</span>
                          )}
                        </td>
                        <td className="px-3 py-3">
                          <div className="flex flex-wrap gap-1.5">
                            {scenarioTags(scenario.source).map((tag) => (
                              <span key={tag} className="inline-flex rounded-full border border-[#2a63f5]/20 bg-[#2a63f5]/5 px-2 py-1 text-xs font-semibold text-[#2a63f5]">
                                {tag}
                              </span>
                            ))}
                          </div>
                        </td>
                        <td className="px-3 py-3">
                          <div className="grid gap-1.5">
                            <button
                              type="button"
                              onClick={() => void toggleStatus(scenario)}
                              className={cn(
                                "w-fit rounded-full border px-2 py-1 text-xs font-semibold whitespace-nowrap",
                                scenario.status === "completed"
                                  ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                                  : "border-zinc-200 bg-zinc-100 text-zinc-700",
                              )}
                              title="Manual HLS completion status"
                            >
                              {scenario.status === "completed" ? "HLS completed" : "HLS pending"}
                            </button>
                            <span
                              className={cn("w-fit rounded-full border px-2 py-1 text-xs font-semibold whitespace-nowrap", recorderBadge.className)}
                              title={recorderBadge.title}
                            >
                              {recorderBadge.label}
                            </span>
                          </div>
                        </td>
                        <td className="px-3 py-3">
                          <div className="flex justify-end gap-2">
                            {isEditing ? (
                              <>
                                <Button size="icon" onClick={() => void saveApprovedEdit(scenario)} aria-label={`Save ${scenario.title}`} title="Save">
                                  <Check className="h-4 w-4" />
                                </Button>
                                <Button size="icon" variant="outline" onClick={() => setApprovedEditId(null)} aria-label="Cancel edit" title="Cancel">
                                  <X className="h-4 w-4" />
                                </Button>
                              </>
                            ) : (
                              <>
                                <Button size="icon" variant="outline" onClick={() => startApprovedEdit(scenario)} aria-label={`Edit ${scenario.title}`} title="Edit">
                                  <Pencil className="h-4 w-4" />
                                </Button>
                                <Button
                                  size="icon"
                                  variant="outline"
                                  className="border-sky-200 text-sky-700 hover:bg-sky-50"
                                  onClick={() => void clearApprovedRecording(scenario)}
                                  disabled={clearingRecordingScenarioId === scenario.id}
                                  aria-label={`Clear recording for ${scenario.title}`}
                                  title="Clear web recording"
                                >
                                  {clearingRecordingScenarioId === scenario.id ? (
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                  ) : (
                                    <BrushCleaning className="h-4 w-4" />
                                  )}
                                </Button>
                                <Button
                                  size="icon"
                                  variant="outline"
                                  className="border-red-200 text-red-600 hover:bg-red-50"
                                  onClick={() => void deleteApproved(scenario)}
                                  aria-label={`Delete ${scenario.title}`}
                                  title="Delete"
                                >
                                  <Trash2 className="h-4 w-4" />
                                </Button>
                              </>
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex flex-col items-end gap-2">
                            <div className="flex items-center justify-end gap-2">
                              <Button
                                size="sm"
                                className="min-w-24"
                                disabled={launchingScenarioId === scenario.id || recordingScenarioId !== null}
                                onClick={() => void handleLaunch(scenario)}
                                title={recordingScenarioId !== null ? "Another scenario is currently recording" : undefined}
                              >
                                {launchingScenarioId === scenario.id ? (
                                  <Loader2 className="h-4 w-4 animate-spin" />
                                ) : (
                                  <Play className="h-4 w-4" />
                                )}
                                {launchingScenarioId === scenario.id ? "Sending…" : "Launch"}
                              </Button>
                              <Button
                                size="icon"
                                variant="outline"
                                onClick={() => toggleApprovedDescription(scenario.id)}
                                aria-label={isDescriptionOpen ? `Collapse ${scenario.title}` : `Expand ${scenario.title}`}
                                title={isDescriptionOpen ? "Collapse details" : "Expand details"}
                              >
                                {isDescriptionOpen ? (
                                  <ChevronUp className="h-4 w-4" />
                                ) : (
                                  <ChevronDown className="h-4 w-4" />
                                )}
                              </Button>
                            </div>
                            {recordingScenarioId === scenario.id ? (
                              recordingSessionStatus === "none" || recordingSessionStatus === "pending" ? (
                                <span className="flex items-center gap-1.5 rounded-md bg-zinc-50 border border-zinc-200 px-3 py-1.5 text-xs font-semibold text-zinc-700">
                                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                  Waiting for recorder...
                                </span>
                              ) : (
                                <span className="flex items-center gap-1.5 rounded-md bg-red-50 border border-red-200 px-3 py-1.5 text-xs font-semibold text-red-700">
                                  <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-red-500" />
                                  Recording…
                                </span>
                              )
                            ) : null}
                          </div>
                        </td>
                      </tr>
                      {isDescriptionOpen ? (
                        <tr key={`${scenario.id}-details`} className="bg-[#2a63f5]/[0.03]">
                          <td colSpan={10} className="px-4 pb-4 pt-0">
                            <div className="grid gap-4 px-5 py-3 md:grid-cols-[1fr_240px]">
                              <div>
                                <p className="text-xs font-semibold uppercase text-black/50">Description</p>
                                <p className="mt-1 leading-6 text-black/75">
                                  {scenario.description || "No description added."}
                                </p>
                              </div>
                              <div>
                                <p className="text-xs font-semibold uppercase text-black/50">Completed By</p>
                                <p className="mt-1 text-sm text-black/75">
                                  {scenario.status === "completed" ? scenario.completed_by_name ?? scenario.completed_by ?? "-" : "-"}
                                </p>
                              </div>
                            </div>
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}

              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>

      {/* ── Setup Recorder Modal ──────────────────────────────────────────── */}
      {isAddingApproved ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          onClick={(event) => {
            if (event.target === event.currentTarget) {
              setIsAddingApproved(false);
              setNewApprovedDraft({ title: "", description: "", test_id: "", sheet_name: null, pre_conditions: "", test_steps: "", expected_result: "" });
            }
          }}
        >
          <div className="w-full max-w-xl rounded-lg border border-black/10 bg-white p-6 shadow-xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-lg font-semibold text-black">Add Scenario</h2>
                <p className="mt-1 text-sm text-black/60">Create a manual high level scenario for this QA project.</p>
              </div>
              <Button
                size="icon"
                variant="outline"
                onClick={() => {
                  setIsAddingApproved(false);
                  setNewApprovedDraft({ title: "", description: "", test_id: "", sheet_name: null, pre_conditions: "", test_steps: "", expected_result: "" });
                }}
                aria-label="Close add scenario dialog"
                title="Close"
              >
                <X className="h-4 w-4" />
              </Button>
            </div>

            <div className="mt-5 space-y-4">
              <div className="space-y-2">
                <label htmlFor="new-scenario-title" className="text-sm font-medium text-black">
                  Scenario Title
                </label>
                <Input
                  id="new-scenario-title"
                  value={newApprovedDraft.title}
                  onChange={(event) => setNewApprovedDraft((current) => ({ ...current, title: event.target.value }))}
                  placeholder="Enter scenario title"
                  autoFocus
                />
              </div>

              <div className="space-y-2">
                <label htmlFor="new-scenario-test-id" className="text-sm font-medium text-black">
                  Test ID
                </label>
                <Input
                  id="new-scenario-test-id"
                  value={newApprovedDraft.test_id ?? ""}
                  onChange={(event) => setNewApprovedDraft((current) => ({ ...current, test_id: event.target.value }))}
                  placeholder="e.g. TC-0001"
                />
              </div>

              <div className="space-y-2">
                <label htmlFor="new-scenario-sheet" className="text-sm font-medium text-black">
                  Sheet Name
                </label>
                <Input
                  id="new-scenario-sheet"
                  value={newApprovedDraft.sheet_name ?? ""}
                  onChange={(event) => setNewApprovedDraft((current) => ({ ...current, sheet_name: event.target.value }))}
                  placeholder="Which worksheet this scenario belongs to"
                />
              </div>

              <div className="space-y-2">
                <label htmlFor="new-scenario-pre-conditions" className="text-sm font-medium text-black">
                  Pre-Conditions
                </label>
                <textarea
                  id="new-scenario-pre-conditions"
                  rows={3}
                  value={newApprovedDraft.pre_conditions ?? ""}
                  onChange={(event) => setNewApprovedDraft((current) => ({ ...current, pre_conditions: event.target.value }))}
                  className="flex w-full resize-none rounded-md border border-black/20 bg-white px-3 py-2 text-sm text-black placeholder:text-black/45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2a63f5]"
                  placeholder="Conditions that must be true before testing"
                />
              </div>

              <div className="space-y-2">
                <label htmlFor="new-scenario-test-steps" className="text-sm font-medium text-black">
                  Test Steps
                </label>
                <textarea
                  id="new-scenario-test-steps"
                  rows={4}
                  value={newApprovedDraft.test_steps ?? ""}
                  onChange={(event) => setNewApprovedDraft((current) => ({ ...current, test_steps: event.target.value }))}
                  className="flex w-full resize-none rounded-md border border-black/20 bg-white px-3 py-2 text-sm text-black placeholder:text-black/45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2a63f5]"
                  placeholder="Numbered steps to execute"
                />
              </div>

              <div className="space-y-2">
                <label htmlFor="new-scenario-expected-result" className="text-sm font-medium text-black">
                  Expected Result
                </label>
                <textarea
                  id="new-scenario-expected-result"
                  rows={3}
                  value={newApprovedDraft.expected_result ?? ""}
                  onChange={(event) => setNewApprovedDraft((current) => ({ ...current, expected_result: event.target.value }))}
                  className="flex w-full resize-none rounded-md border border-black/20 bg-white px-3 py-2 text-sm text-black placeholder:text-black/45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2a63f5]"
                  placeholder="Expected outcome after executing the steps"
                />
              </div>
            </div>

            <div className="mt-6 flex justify-end gap-2">
              <Button
                variant="outline"
                onClick={() => {
                  setIsAddingApproved(false);
                  setNewApprovedDraft({ title: "", description: "", test_id: "", sheet_name: null, pre_conditions: "", test_steps: "", expected_result: "" });
                }}
              >
                <X className="h-4 w-4" />
                Cancel
              </Button>
              <Button onClick={() => void createApproved()}>
                <Check className="h-4 w-4" />
                Save Scenario
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      {showSetupModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          onClick={(e) => { if (e.target === e.currentTarget) setShowSetupModal(false); }}
        >
          <div className="relative w-full max-w-2xl rounded-xl border border-black/10 bg-white p-6 shadow-xl">
            <button
              type="button"
              className="absolute right-4 top-4 rounded-md p-1 text-black/50 hover:text-black"
              onClick={() => setShowSetupModal(false)}
              aria-label="Close"
            >
              <X className="h-5 w-5" />
            </button>

            <h2 className="text-lg font-semibold text-black">Start the Recorder Daemon</h2>
            <p className="mt-1 text-sm text-black/60">
              Run this command on your local machine. It downloads the recorder script, installs
              Playwright, and starts the daemon — which will listen for Launch signals from this
              dashboard.
            </p>

            {isLoadingSetup ? (
              <div className="mt-6 flex justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-[#2a63f5]" />
              </div>
            ) : setupInfo ? (
              <>
                <div className="mt-4 overflow-hidden rounded-lg border border-black/10 bg-black/[0.03]">
                  <div className="flex border-b border-black/10 bg-black/5">
                    <button
                      type="button"
                      onClick={() => setSetupTab("mac")}
                      className={cn("px-4 py-2 text-sm font-medium", setupTab === "mac" ? "bg-white text-black border-b-2 border-[#2a63f5]" : "text-black/60 hover:text-black")}
                    >
                      Mac / Linux
                    </button>
                    <button
                      type="button"
                      onClick={() => setSetupTab("windows")}
                      className={cn("px-4 py-2 text-sm font-medium", setupTab === "windows" ? "bg-white text-black border-b-2 border-[#2a63f5]" : "text-black/60 hover:text-black")}
                    >
                      Windows
                    </button>
                  </div>
                  <div className="p-4">
                    <p className="break-all font-mono text-sm text-black">
                      {setupTab === "mac" ? setupInfo.mac_setup_command : setupInfo.windows_setup_command}
                    </p>
                  </div>
                </div>
                <div className="mt-3 flex items-center gap-3">
                  <Button onClick={handleCopySetup} className="gap-2">
                    {copiedSetup ? <Check className="h-4 w-4" /> : <Save className="h-4 w-4" />}
                    {copiedSetup ? "Copied!" : "Copy Command"}
                  </Button>
                  <p className="text-xs text-black/50">
                    Token: <span className="font-mono">{setupInfo.recorder_token.slice(0, 8)}…</span>
                  </p>
                </div>
                <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                  <strong>After starting the daemon,</strong> click <strong>Launch</strong> next to any
                  scenario in this table. Chromium will open on your machine within ~1 second.
                </div>
              </>
            ) : (
              <p className="mt-4 text-sm text-red-600">Failed to load setup information. Please try again.</p>
            )}
          </div>
        </div>
      )}
    </>
  );
}
