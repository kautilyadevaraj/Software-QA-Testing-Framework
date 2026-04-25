"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { Check, ChevronDown, ChevronUp, Info, Loader2, Pencil, Play, Plus, Save, Trash2, WandSparkles, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  ApiError,
  approveHighLevelScenarios,
  createHighLevelScenario,
  deleteHighLevelScenario,
  generateHighLevelScenarios,
  listHighLevelScenarios,
  updateHighLevelScenario,
  type ScenarioAccessMode,
  type ScenarioLevel,
  type ScenarioGenerationType,
  type HighLevelScenario,
  type PreviewScenario,
  type ScenarioSource,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type ScenarioQaPanelProps = {
  projectId: string;
  currentUserId: string | null;
};

type DraftScenario = {
  title: string;
  description: string;
};

const GENERATION_STEPS = [
  "Agent 1: reading BRD / WBS / FSD / assumptions",
  "Agent 2: reading Swagger / OpenAPI docs",
  "Deduplication: merging and trimming tester scenarios",
];

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

function tableInputClass() {
  return "min-h-10 w-full rounded-md border border-black/20 bg-white px-3 py-2 text-sm text-black focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2a63f5]";
}

export function ScenarioQaPanel({ projectId, currentUserId }: ScenarioQaPanelProps) {
  const [approvedScenarios, setApprovedScenarios] = useState<HighLevelScenario[]>([]);
  const [previewScenarios, setPreviewScenarios] = useState<PreviewScenario[]>([]);
  const [isLoadingApproved, setIsLoadingApproved] = useState(true);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isApproving, setIsApproving] = useState(false);
  const [activeStep, setActiveStep] = useState(0);
  const [completedSteps, setCompletedSteps] = useState<number[]>([]);
  const [previewEditIndex, setPreviewEditIndex] = useState<number | null>(null);
  const [previewDraft, setPreviewDraft] = useState<DraftScenario>({ title: "", description: "" });
  const [approvedEditId, setApprovedEditId] = useState<string | null>(null);
  const [approvedDraft, setApprovedDraft] = useState<DraftScenario>({ title: "", description: "" });
  const [isAddingApproved, setIsAddingApproved] = useState(false);
  const [newApprovedDraft, setNewApprovedDraft] = useState<DraftScenario>({ title: "", description: "" });
  const [scenarioLimit, setScenarioLimit] = useState<(typeof SCENARIO_LIMIT_OPTIONS)[number]["value"]>("20");
  const [customScenarioCount, setCustomScenarioCount] = useState("35");
  const [scenarioTypes, setScenarioTypes] = useState<ScenarioGenerationType[]>(["ALL"]);
  const [accessMode, setAccessMode] = useState<ScenarioAccessMode>("UI_ONLY_WEB");
  const [scenarioLevel, setScenarioLevel] = useState<ScenarioLevel>("HLS");
  const [isOptionsGuideOpen, setIsOptionsGuideOpen] = useState(false);
  const [expandedDescriptionIds, setExpandedDescriptionIds] = useState<string[]>([]);

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

  useEffect(() => {
    void loadApprovedScenarios();
  }, [loadApprovedScenarios]);

  const startPreviewEdit = (index: number) => {
    const scenario = previewScenarios[index];
    setPreviewEditIndex(index);
    setPreviewDraft({ title: scenario.title, description: scenario.description });
  };

  const savePreviewEdit = () => {
    if (previewEditIndex === null) return;
    if (!previewDraft.title.trim()) {
      toast.error("Scenario title is required.");
      return;
    }
    setPreviewScenarios((current) =>
      current.map((scenario, index) =>
        index === previewEditIndex
          ? { ...scenario, title: previewDraft.title.trim(), description: previewDraft.description.trim() }
          : scenario,
      ),
    );
    setPreviewEditIndex(null);
  };

  const handleGenerate = async () => {
    setIsGenerating(true);
    setActiveStep(1);
    setCompletedSteps([]);
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

    const request = generateHighLevelScenarios(projectId, {
      max_scenarios: maxScenarios,
      scenario_types: scenarioTypes,
      access_mode: accessMode,
      scenario_level: scenarioLevel,
      existing_scenarios: existingScenarios,
    });
    try {
      setActiveStep(1);
      await sleep(1200);
      setCompletedSteps([1]);
      setActiveStep(2);
      await sleep(1200);
      setCompletedSteps([1, 2]);
      setActiveStep(3);
      const response = await request;
      setCompletedSteps([1, 2, 3]);
      setPreviewScenarios((current) => [...current, ...response.scenarios]);
      if (response.scenarios.length === 0) {
        toast.info(existingScenarios.length > 0 ? "No additional scenarios were found." : "No scenarios were generated from the ingested chunks.");
      } else {
        toast.success(`Generated ${response.scenarios.length} additional high level scenarios.`);
      }
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Scenario generation failed.");
    } finally {
      setIsGenerating(false);
      setActiveStep(0);
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
    setPreviewScenarios((current) => [...current, { title: "", description: "", source: "manual" }]);
    setPreviewEditIndex(previewScenarios.length);
    setPreviewDraft({ title: "", description: "" });
  };

  const handleApprove = async () => {
    const clean = previewScenarios
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
      setPreviewScenarios([]);
      await loadApprovedScenarios();
      toast.success(`Saved ${response.saved} scenarios.`);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Unable to save scenarios.");
    } finally {
      setIsApproving(false);
    }
  };

  const startApprovedEdit = (scenario: HighLevelScenario) => {
    setApprovedEditId(scenario.id);
    setApprovedDraft({ title: scenario.title, description: scenario.description });
  };

  const saveApprovedEdit = async (scenario: HighLevelScenario) => {
    if (!approvedDraft.title.trim()) {
      toast.error("Scenario title is required.");
      return;
    }
    try {
      const updated = await updateHighLevelScenario(projectId, scenario.id, {
        title: approvedDraft.title.trim(),
        description: approvedDraft.description.trim(),
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

  const createApproved = async () => {
    if (!newApprovedDraft.title.trim()) {
      toast.error("Scenario title is required.");
      return;
    }
    try {
      const saved = await createHighLevelScenario(projectId, {
        title: newApprovedDraft.title.trim(),
        description: newApprovedDraft.description.trim(),
      });
      setApprovedScenarios((current) => [...current, saved]);
      setNewApprovedDraft({ title: "", description: "" });
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

      {isGenerating ? (
        <div className="mt-4 grid gap-2">
          {GENERATION_STEPS.map((label, index) => {
            const step = index + 1;
            const isDone = completedSteps.includes(step);
            const isActive = activeStep === step && !isDone;
            return (
              <div key={label} className="flex items-center gap-3 rounded-md border border-black/10 px-3 py-2 text-sm">
                <span className="font-semibold text-black/60">Step {step}</span>
                <span className="flex-1 text-black">{label}</span>
                {isDone ? (
                  <Check className="h-4 w-4 text-emerald-600" />
                ) : isActive ? (
                  <Loader2 className="h-4 w-4 animate-spin text-[#2a63f5]" />
                ) : (
                  <span className="h-4 w-4 rounded-full border border-black/20" />
                )}
              </div>
            );
          })}
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
    <div className="space-y-6">
      {generationSettingsCard}

      {previewScenarios.length > 0 ? (
        <div className="overflow-hidden rounded-lg border border-black/10 bg-white">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-black/10 px-4 py-3">
            <div>
              <h2 className="text-base font-semibold text-black">Scenario Preview</h2>
              <p className="text-sm text-black/60">Edits here stay in memory until approval.</p>
            </div>
            <div className="flex flex-wrap gap-2">
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
            <table className="w-full min-w-[1080px] table-fixed text-left text-sm">
              <thead className="bg-black/[0.03] text-xs uppercase text-black/60">
                <tr>
                  <th className="w-12 px-4 py-3">#</th>
                  <th className="w-[260px] px-4 py-3">Title</th>
                  <th className="px-4 py-3">Description</th>
                  <th className="w-[190px] px-4 py-3">Tags</th>
                  <th className="w-[120px] px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {previewScenarios.map((scenario, index) => {
                  const isEditing = previewEditIndex === index;
                  return (
                    <tr key={`${scenario.source}-${index}`}>
                      <td className="px-4 py-3 text-black/60">{index + 1}</td>
                      <td className="px-4 py-3">
                        {isEditing ? (
                          <Input value={previewDraft.title} onChange={(e) => setPreviewDraft((current) => ({ ...current, title: e.target.value }))} />
                        ) : (
                          <span className="block font-medium leading-6 text-black">{scenario.title}</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {isEditing ? (
                          <textarea
                            rows={2}
                            value={previewDraft.description}
                            onChange={(e) => setPreviewDraft((current) => ({ ...current, description: e.target.value }))}
                            className={tableInputClass()}
                          />
                        ) : (
                          <span className="block leading-6 text-black/70">{scenario.description}</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-wrap gap-1.5">
                          {scenarioTags(scenario.source).map((tag) => (
                            <span key={tag} className="inline-flex rounded-full border border-[#2a63f5]/20 bg-[#2a63f5]/5 px-2 py-1 text-xs font-semibold text-[#2a63f5]">
                              {tag}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td className="px-4 py-3">
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
              <p className="text-sm text-black/60">{approvedScenarios.length} scenarios ready for tester review.</p>
            </div>
            <Button variant="outline" onClick={() => setIsAddingApproved(true)}>
              <Plus className="h-4 w-4" />
              Add Scenario
            </Button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] table-fixed text-left text-sm">
              <thead className="bg-black/[0.03] text-xs uppercase text-black/60">
                <tr>
                  <th className="w-24 px-4 py-3">ID</th>
                  <th className="px-4 py-3">Title</th>
                  <th className="w-32 px-4 py-3">Tags</th>
                  <th className="w-32 px-4 py-3">Status</th>
                  <th className="w-[112px] px-4 py-3 text-right">Actions</th>
                  <th className="w-[180px] px-4 py-3 text-right">Launch</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-black/10">
                {approvedScenarios.map((scenario) => {
                  const isEditing = approvedEditId === scenario.id;
                  const isDescriptionOpen = expandedDescriptionIds.includes(scenario.id);
                  return (
                    <Fragment key={scenario.id}>
                      <tr className={cn("border-t border-black/10", isDescriptionOpen ? "bg-[#2a63f5]/[0.03]" : undefined)}>
                        <td className="px-4 py-3 font-mono text-xs text-black/60" title={scenario.id}>
                          {scenario.id.slice(0, 8)}
                        </td>
                        <td className="px-4 py-3">
                          {isEditing ? (
                            <div className="grid gap-2">
                              <Input value={approvedDraft.title} onChange={(e) => setApprovedDraft((current) => ({ ...current, title: e.target.value }))} />
                              <textarea
                                rows={2}
                                value={approvedDraft.description}
                                onChange={(e) => setApprovedDraft((current) => ({ ...current, description: e.target.value }))}
                                className={tableInputClass()}
                              />
                            </div>
                          ) : (
                            <span className="block font-medium leading-6 text-black">{scenario.title}</span>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex flex-wrap gap-1.5">
                            {scenarioTags(scenario.source).map((tag) => (
                              <span key={tag} className="inline-flex rounded-full border border-[#2a63f5]/20 bg-[#2a63f5]/5 px-2 py-1 text-xs font-semibold text-[#2a63f5]">
                                {tag}
                              </span>
                            ))}
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          <button
                            type="button"
                            onClick={() => void toggleStatus(scenario)}
                            className={cn(
                              "rounded-full border px-2 py-1 text-xs font-semibold",
                              scenario.status === "completed"
                                ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                                : "border-zinc-200 bg-zinc-100 text-zinc-700",
                            )}
                          >
                            {scenario.status === "completed" ? "Completed" : "Pending"}
                          </button>
                        </td>
                        <td className="px-4 py-3">
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
                          <div className="flex items-center justify-end gap-2">
                            <Button
                              size="sm"
                              className="min-w-24"
                              onClick={() => {
                                toast.info(`Launching scenario: ${scenario.title}`);
                                console.log({ scenario_id: scenario.id, project_id: projectId });
                              }}
                            >
                              <Play className="h-4 w-4" />
                              Launch
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
                        </td>
                      </tr>
                      {isDescriptionOpen ? (
                        <tr key={`${scenario.id}-details`} className="bg-[#2a63f5]/[0.03]">
                          <td colSpan={6} className="px-4 pb-4 pt-0">
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

                {isAddingApproved ? (
                  <tr>
                    <td className="px-4 py-3 text-black/40">New</td>
                    <td className="px-4 py-3">
                      <Input value={newApprovedDraft.title} onChange={(e) => setNewApprovedDraft((current) => ({ ...current, title: e.target.value }))} placeholder="Scenario title" />
                    </td>
                    <td className="px-4 py-3" colSpan={4}>
                      <textarea
                        rows={2}
                        value={newApprovedDraft.description}
                        onChange={(e) => setNewApprovedDraft((current) => ({ ...current, description: e.target.value }))}
                        className={tableInputClass()}
                        placeholder="Scenario description"
                      />
                    </td>
                    <td className="px-4 py-3 text-right" colSpan={2}>
                      <div className="flex justify-end gap-2">
                        <Button size="sm" onClick={() => void createApproved()}>
                          <Check className="h-4 w-4" />
                          Save
                        </Button>
                        <Button size="sm" variant="outline" onClick={() => setIsAddingApproved(false)}>
                          <X className="h-4 w-4" />
                          Cancel
                        </Button>
                      </div>
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
}
