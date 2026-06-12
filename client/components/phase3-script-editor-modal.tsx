"use client";

import { useEffect, useState } from "react";
import { Loader2, Save, X } from "lucide-react";
import { toast } from "sonner";
import CodeMirror from "@uiw/react-codemirror";
import { javascript } from "@codemirror/lang-javascript";
import { Button } from "@/components/ui/button";
import {
  ApiError,
  getPhase3Script,
  rerunPhase3ReviewItem,
  type Phase3ReviewItem,
} from "@/lib/api";

const CM_EXTENSIONS = [javascript({ typescript: true })];

type Props = {
  open: boolean;
  onClose: () => void;
  projectId: string;
  item: Phase3ReviewItem;
  onRerun: (updated: Phase3ReviewItem) => void;
};

export function Phase3ScriptEditorModal({ open, onClose, projectId, item, onRerun }: Props) {
  const [script, setScript] = useState("");
  const [original, setOriginal] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    getPhase3Script(projectId, item.test_id)
      .then((res) => {
        setScript(res.script_content);
        setOriginal(res.script_content);
      })
      .catch((err) => {
        toast.error(err instanceof ApiError ? err.message : "Failed to load script");
        onClose();
      })
      .finally(() => setLoading(false));
  }, [open, projectId, item.test_id, onClose]);

  const isDirty = script !== original;

  function handleClose() {
    if (isDirty && !confirm("You have unsaved changes. Close anyway?")) return;
    onClose();
  }

  async function handleSaveAndRerun() {
    setSaving(true);
    try {
      const updated = await rerunPhase3ReviewItem(projectId, item.id, script);
      toast.success("Script saved — test re-queued");
      setOriginal(script);
      onRerun(updated);
      onClose();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to save & re-run");
    } finally {
      setSaving(false);
    }
  }

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div
        className="flex w-full max-w-5xl flex-col rounded-xl bg-white shadow-2xl"
        style={{ maxHeight: "92vh" }}
      >
        {/* ── Header ─────────────────────────────────────────────────────── */}
        <div className="flex items-center justify-between border-b border-gray-200 px-5 py-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <p className="text-xs font-semibold uppercase tracking-widest text-gray-400">
                Script Editor
              </p>
              {isDirty && (
                <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-700">
                  unsaved
                </span>
              )}
            </div>
            <p className="mt-0.5 truncate font-mono text-sm text-gray-700">
              {item.test_id}
            </p>
          </div>
          <button
            onClick={handleClose}
            className="ml-4 shrink-0 rounded p-1.5 hover:bg-gray-100"
            aria-label="Close"
          >
            <X className="h-5 w-5 text-gray-500" />
          </button>
        </div>

        {/* ── Body — CodeMirror 6 ────────────────────────────────────────── */}
        <div className="flex-1 overflow-auto">
          {loading ? (
            <div className="flex h-64 items-center justify-center">
              <Loader2 className="h-7 w-7 animate-spin text-gray-400" />
            </div>
          ) : (
            <CodeMirror
              value={script}
              extensions={CM_EXTENSIONS}
              theme="dark"
              onChange={setScript}
              basicSetup={{
                lineNumbers: true,
                foldGutter: true,
                highlightActiveLine: true,
                autocompletion: true,
                indentOnInput: true,
              }}
              style={{ fontSize: 13 }}
            />
          )}
        </div>

        {/* ── Footer ─────────────────────────────────────────────────────── */}
        <div className="flex items-center justify-between border-t border-gray-200 bg-gray-50 px-5 py-3">
          <p className="text-xs text-gray-400">
            {isDirty
              ? "Edit the script above, then click Save & Re-run to re-queue."
              : "No changes — edit the script to enable Save & Re-run."}
          </p>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={handleClose} disabled={saving}>
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={handleSaveAndRerun}
              disabled={saving || loading || !isDirty}
              className="gap-1.5"
            >
              {saving ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Save className="h-3.5 w-3.5" />
              )}
              Save &amp; Re-run
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
