"use client";

import { useState } from "react";
import { Loader2, Ticket, ExternalLink, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  ApiError,
  createTicket,
  type JiraTicketResponse,
  type RaiseTicketPayload,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type RaiseTicketModalProps = {
  open: boolean;
  onClose: () => void;
  projectId: string;
  defaultTitle: string;
  defaultDescription: string;
  raisedFrom: "url_section" | "credentials_section";
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function RaiseTicketModal({
  open,
  onClose,
  projectId,
  defaultTitle,
  defaultDescription,
  raisedFrom,
}: RaiseTicketModalProps) {
  const [title, setTitle] = useState(defaultTitle);
  const [description, setDescription] = useState(defaultDescription);
  const [issueType, setIssueType] = useState<RaiseTicketPayload["issue_type"]>("Bug");
  const [priority, setPriority] = useState<RaiseTicketPayload["priority"]>("Medium");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [raisedTicket, setRaisedTicket] = useState<JiraTicketResponse | null>(null);

  if (!open) return null;

  const handleClose = () => {
    // Reset state when closing so next open is fresh
    setTitle(defaultTitle);
    setDescription(defaultDescription);
    setIssueType("Bug");
    setPriority("Medium");
    setIsSubmitting(false);
    setRaisedTicket(null);
    onClose();
  };

  const handleSubmit = async () => {
    if (!title.trim()) {
      toast.error("Title is required.");
      return;
    }

    setIsSubmitting(true);
    try {
      const result = await createTicket(projectId, {
        title: title.trim(),
        description: description.trim(),
        issue_type: issueType,
        priority,
        raised_from: raisedFrom,
      });
      setRaisedTicket(result);
      toast.success(`Ticket ${result.jira_issue_key} raised on Jira ✔`);
    } catch (error) {
      const message =
        error instanceof ApiError ? error.message : "Failed to raise ticket.";
      toast.error(message);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm"
        onClick={handleClose}
        aria-hidden="true"
      />

      {/* Modal panel */}
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="raise-ticket-modal-title"
        className="fixed inset-0 z-50 flex items-center justify-center p-4"
      >
        <div className="w-full max-w-lg rounded-xl border border-black/10 bg-white shadow-2xl">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-black/10 px-6 py-4">
            <div className="flex items-center gap-2">
              <Ticket className="h-5 w-5 text-[#2a63f5]" />
              <h2
                id="raise-ticket-modal-title"
                className="text-base font-semibold text-black"
              >
                Raise Jira Ticket
              </h2>
            </div>
            <button
              type="button"
              onClick={handleClose}
              aria-label="Close modal"
              className="rounded-md p-1.5 text-black/50 hover:bg-black/5 hover:text-black"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* Success state */}
          {raisedTicket ? (
            <div className="flex flex-col items-center gap-4 px-6 py-10 text-center">
              <div className="flex h-14 w-14 items-center justify-center rounded-full bg-emerald-50 ring-2 ring-emerald-200">
                <Ticket className="h-7 w-7 text-emerald-600" />
              </div>
              <div>
                <p className="text-lg font-semibold text-black">
                  Ticket{" "}
                  <span className="text-[#2a63f5]">{raisedTicket.jira_issue_key}</span>{" "}
                  raised!
                </p>
                <p className="mt-1 text-sm text-black/60">{raisedTicket.title}</p>
              </div>
              <div className="flex items-center gap-2 rounded-md border border-black/10 px-3 py-1.5 text-xs text-black/50">
                <span className="font-medium text-black/70">
                  {raisedTicket.issue_type}
                </span>
                <span>·</span>
                <span>{raisedTicket.priority} Priority</span>
                <span>·</span>
                <span className="capitalize">
                  {raisedTicket.raised_from.replace("_", " ")}
                </span>
              </div>
              <Button
                variant="outline"
                size="sm"
                className="mt-2 gap-2"
                onClick={handleClose}
              >
                <ExternalLink className="h-3.5 w-3.5" />
                Done
              </Button>
            </div>
          ) : (
            /* Form state */
            <div className="space-y-5 px-6 py-5">
              {/* Title */}
              <div className="space-y-2">
                <Label htmlFor="ticket-title">
                  Title <span className="text-red-500">*</span>
                </Label>
                <Input
                  id="ticket-title"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="Brief summary of the issue"
                  disabled={isSubmitting}
                />
              </div>

              {/* Description */}
              <div className="space-y-2">
                <Label htmlFor="ticket-description">Description</Label>
                <textarea
                  id="ticket-description"
                  rows={4}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Steps to reproduce, expected vs actual behaviour…"
                  disabled={isSubmitting}
                  className="flex w-full resize-none rounded-md border border-black/20 bg-white px-3 py-2 text-sm text-black placeholder:text-black/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2a63f5] disabled:opacity-50"
                />
              </div>

              {/* Issue type + Priority (side by side) */}
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="ticket-issue-type">Issue Type</Label>
                  <select
                    id="ticket-issue-type"
                    value={issueType}
                    onChange={(e) =>
                      setIssueType(e.target.value as RaiseTicketPayload["issue_type"])
                    }
                    disabled={isSubmitting}
                    className="flex w-full rounded-md border border-black/20 bg-white px-3 py-2 text-sm text-black focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2a63f5] disabled:opacity-50"
                  >
                    <option value="Bug">🐛 Bug</option>
                    <option value="Task">✅ Task</option>
                    <option value="Story">📖 Story</option>
                  </select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="ticket-priority">Priority</Label>
                  <select
                    id="ticket-priority"
                    value={priority}
                    onChange={(e) =>
                      setPriority(e.target.value as RaiseTicketPayload["priority"])
                    }
                    disabled={isSubmitting}
                    className="flex w-full rounded-md border border-black/20 bg-white px-3 py-2 text-sm text-black focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2a63f5] disabled:opacity-50"
                  >
                    <option value="High">🔴 High</option>
                    <option value="Medium">🟡 Medium</option>
                    <option value="Low">🟢 Low</option>
                  </select>
                </div>
              </div>

              {/* Context badge (read-only info) */}
              <p className="text-xs text-black/45">
                Raised from:{" "}
                <span className="font-medium text-black/60">
                  {raisedFrom === "url_section"
                    ? "URL Verification"
                    : "Credentials Verification"}
                </span>
              </p>

              {/* Actions */}
              <div className="flex justify-end gap-2 border-t border-black/10 pt-4">
                <Button
                  type="button"
                  variant="outline"
                  onClick={handleClose}
                  disabled={isSubmitting}
                >
                  Cancel
                </Button>
                <Button
                  type="button"
                  onClick={handleSubmit}
                  disabled={isSubmitting}
                  className="bg-[#2a63f5] text-white hover:bg-[#2a63f5]/90"
                >
                  {isSubmitting ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      Raising…
                    </>
                  ) : (
                    <>
                      <Ticket className="mr-2 h-4 w-4" />
                      Raise Ticket on Jira
                    </>
                  )}
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
